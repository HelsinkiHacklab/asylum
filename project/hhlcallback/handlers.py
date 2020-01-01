# -*- coding: utf-8 -*-
import calendar
import datetime
import logging

import environ
import holvirc
from creditor.handlers import BaseRecurringTransactionsHandler, BaseTransactionHandler
from creditor.models import Transaction, TransactionTag
from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from holviapi.utils import barcode as bank_barcode
from holviapp.utils import get_connection as get_holvi_connection
from members.handlers import BaseApplicationHandler, BaseMemberHandler
from slacksync.utils import quick_invite

from .utils import get_nordea_payment_reference, get_nordea_yearly_payment_reference

logger = logging.getLogger('hhlcallback.handlers')
env = environ.Env()


class ApplicationHandler(BaseApplicationHandler):
    fresh_emails = {}

    def on_saving(self, instance, *args, **kwargs):
        """Called just before passing control to save()"""
        if not instance.pk:
            self.fresh_emails[instance.email] = True

    def on_saved(self, instance, *args, **kwargs):
        """Called after save() returns"""
        if instance.email in self.fresh_emails:
            # Just created
            del(self.fresh_emails[instance.email])
            mail = EmailMessage()
            mail.from_email = '"%s" <%s>' % (instance.name, instance.email)
            mail.to = ["hallitus@helsinki.hacklab.fi", ]
            mail.subject = "Jäsenhakemus: %s" % instance.name
            mail.body = "Uusi hakemus Asylumissa: https://lataamo.hacklab.fi/admin/members/membershipapplication/%d/" % instance.pk
            mail.send()
        pass

    def on_approved(self, application, member):
        rest_of_year_free = False
        fee_msg_fi = "Jäsenmaksusta tulee sinulle erillinen viesti."
        fee_msg_en = "You will receive a separate message about membership fee."
        # If application was received in Q4, rest of this year is free
        if application.received.month >= 10:
            rest_of_year_free = True
            fee_msg_fi = "Vuoden %s jäsenmaksua ei peritä." % application.received.year
            fee_msg_en = "Membership fee for year %s has been waived." % application.received.year

        # Auto-add the membership fee as recurring transaction
        membership_fee = env.float('HHL_MEMBERSHIP_FEE', default=None)
        membership_tag = 1
        if membership_fee and membership_tag:
            from creditor.models import RecurringTransaction, TransactionTag
            rt = RecurringTransaction()
            rt.tag = TransactionTag.objects.get(pk=membership_tag)
            rt.owner = member
            rt.amount = -membership_fee
            rt.rtype = RecurringTransaction.YEARLY
            # If application was received in Q4 set the recurringtransaction to start from next year
            if rest_of_year_free:
                rt.start = datetime.date(year=application.received.year + 1, month=1, day=1)
            rt.save()
            # Skip this, 1. speed 2. if it fails due to holvi bullshittery we can't approve members...
            # rt.conditional_add_transaction()

        # Subscribe to mailing list
        mailman_subscribe = env('HHL_MAILMAN_SUBSCRIBE', default=None)
        if mailman_subscribe:
            mail = EmailMessage()
            mail.from_email = member.email
            mail.to = [mailman_subscribe, ]
            mail.subject = 'subscribe'
            mail.body = 'subscribe'
            mail.send()

        # Invite to Slack
        # Done with the shared invite in the welcome-email
        # quick_invite(member.email)

        # Welcome-email
        mail = EmailMessage()
        mail.to = [member.email, ]
        mail.from_email = "hallitus@helsinki.hacklab.fi"
        mail.subject = "Helsinki Hacklabin jäsenhakemus hyväksytty! | Your membership application has been approved!"
        mail.body = """Tervetuloa Helsinki Hacklabin jäseneksi!

Jäsenenä olet tervetullut kaikkiin Helsinki Hacklabin tilaisuuksiin, kuten tiistaisiin jäseniltoihin, torstaisiin kurssipäiviin, sekä muihin järjestettäviin tapahtumiin.

Käytä alla olevaa linkkiä liittyäksesi Helsinki Hacklabin omaan Slack-ryhmään. Slackissa on jäsenten yleinen keskustelu ja omia kanaviaan eri aihealueista.
Lisätietoja kanavista, boteista, yms Wikissä: http://wiki.helsinki.hacklab.fi/Slack

{slacklink}

Hacklabien yhteisellä foorumilla https://discourse.hacklab.fi/ pääset keskustelemaan sekä muiden jäsenten että kaikkien Suomen hacklabien kanssa.

{fee_msg_fi}

Jos sinulla on jäsenyyteen tai tähän viestiin liittyviä kysymyksiä, voit lähettää ne Helsinki Hacklabin hallitukselle vastaamalla tähän viestiin tai lähettämällä viestin osoitteeseen hallitus@helsinki.hacklab.fi

----

Helsinki Hacklab welcomes you as a member!

As a member you are most welcome to attend all events organised by Helsinki Hacklab. These include the weekly member gathering every Tuesday, our courses and workshops held on Thurdays and other events we might come up with.

Use the link below to join the Helsinki Hacklab Slack group. Slack has general chat for members and more channels for specific topics.
More information about channels, bots, etc in our Wiki: http://wiki.helsinki.hacklab.fi/Slack

{slacklink}

You can reach our members and all Finnish Hackerspaces in our shared forum https://discourse.hacklab.fi/.

{fee_msg_en}

For questions regarding your membership or this message, please contact the board of Helsinki Hacklab at hallitus@helsinki.hacklab.fi or simply reply to this message.

""".format(fee_msg_fi=fee_msg_fi, fee_msg_en=fee_msg_en, slacklink=settings.SLACK_INVITE_LINK)
        mail.send()


class RecurringTransactionsHolviHandler(BaseRecurringTransactionsHandler):
    category_maps = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def on_creating(self, rt, t, *args, **kwargs):
        # Only negative amounts go to invoices
        if t.amount >= 0.0:
            return True
        if int(t.tag.tmatch) == 1:  # Membership feee
            return self.send_membershipfee_email(rt, t)
        if int(t.tag.tmatch) == 2:  # Keyholder feee
            return self.send_keyholder_fee_email(rt, t)
        return True

    def is_first_keyholder_email(self, rt, t):
        # If we have positive amounts paid with this reference and amount, then is not the first mail.
        if Transaction.objects.filter(owner=t.owner, amount=-t.amount, reference=t.reference).count():
            return False
        return True

    def send_keyholder_fee_email(self, rt, t):
        t.reference = get_nordea_payment_reference(t.owner.member_id, int(t.tag.tmatch))
        # Skip sending if this is not the first transaction with this sum
        if not self.is_first_keyholder_email(rt, t):
            return True

        iban = env('NORDEA_BARCODE_IBAN')
        member = t.owner
        mail = EmailMessage()
        mail.to = [member.email, ]
        mail.from_email = "hallitus@helsinki.hacklab.fi"
        mail.subject = "Avainjäsenen maksutiedot | Keyholder membership payment info"
        mail.body = """
(In English below)

Tässä avainjäsenyyden maksutietosi Helsinki Hacklab ry:lle. Vuosijäsenmaksut ovat eri asia ja maksetaan eri viitteellä.

Virtuaaliviivakoodi sisältää kaikki maksua koskevat tiedot, ja se on suositeltavin tapa syöttää maksutiedot oikein
verkkopankkiisi. Muista tehdä maksusta kuukausittain toistuva. Verkkopankissa on jossain "lue viivakoodi" -tyyppinen nappi,
paina siitä ja kopioi viivakoodin numerosarja annettuun kenttään.

Virtuaaliviivakoodisi:
{barcode}


Maksaminen manuaalisesti tiedot syöttämällä.

Ole huolellinen maksutietojen syöttämisessä, jos et käytä virtuaaliviivakoodia. Yhdistyksellä on kaksi eri tilinumeroa.
Ole hyvä, ja varmista, että maksut siirtyvät Nordea-tilillemme viitteen kanssa. Jätä maksun viestikenttä tyhjäksi.

Tilinumero: {iban}
Viitenumerosi: {ref}
Maksun summa: {sum}EUR
Eräpäivä: valitse päivämäärä esimerkiksi kuukauden alkupuolelta (suositus)
Toistuvuus: joka kuukausi

Nämä maksutiedot ovat toistaiseksi voimassa ja niitä koskevat muutokset ilmoitetaan erikseen.

----

This is the payment information for your keyholder membership at Helsinki Hacklab ry. Yearly membership fees are handled
separately, with different reference.

Virtual barcode (usable with Finnish banks) contains all the information required to make a payment and is
the recommended way to input the payment information. Remember to make the payment repeat monthly. In the netbank there
should be "read barcode" (or similar) button, copy the barcode numbers to the dialog given after clicking the button.

Virtual barcode:
{barcode}

Manual input of payment information.

Pay extra attention when entering payment info manually. Helsinki Hacklab has two different accounts, please make sure
you are using our Nordea account and the correct reference number. Leave the message-field empty.

Account number: {iban}
Reference number: {ref}
Amount due: {sum}EUR
Due date: Choose a date that suits you, preferably early in the first half of the month.
Repeat payment: Monthly

This payment information is valid until further notice, you will be sent notification of changes.
        """.format(
            iban=iban,
            ref=t.reference,
            sum=-t.amount,
            barcode=bank_barcode(iban, t.reference, -t.amount)
        )
        try:
            mail.send()
        except Exception as e:
            logger.exception("Failed to send payment-info email to {}".format(member.email))

        return True

    def send_membershipfee_email(self, rt, t):
        if not t.stamp:
            t.stamp = datetime.datetime.now()
        duedate = t.stamp + datetime.timedelta(weeks=2)
        year = t.stamp.year
        t.reference = get_nordea_yearly_payment_reference(t.owner.member_id, int(t.tag.tmatch), year)

        iban = env('NORDEA_BARCODE_IBAN')
        member = t.owner
        mail = EmailMessage()
        mail.to = [member.email, ]
        mail.from_email = "hallitus@helsinki.hacklab.fi"
        mail.subject = "Helsinki Hacklab ry vuoden {year} jäsenyyden maksutiedot | Membership payment info for year {year}".format(year=year)
        mail.body = """
(In English below)

Tässä vuoden {year} jäsenmaksun maksutietosi Helsinki Hacklab ry:lle. Avainmaksut ovat eri asia ja maksetaan eri viitteellä.

Virtuaaliviivakoodi sisältää kaikki maksua koskevat tiedot, ja se on suositeltavin tapa syöttää maksutiedot oikein
verkkopankkiisi. Verkkopankissa on jossain "lue viivakoodi" -tyyppinen nappi,
paina siitä ja kopioi viivakoodin numerosarja annettuun kenttään.

Virtuaaliviivakoodisi:
{barcode}


Maksaminen manuaalisesti tiedot syöttämällä.

Ole huolellinen maksutietojen syöttämisessä, jos et käytä virtuaaliviivakoodia. Yhdistyksellä on kaksi eri tilinumeroa.
Ole hyvä, ja varmista, että maksut siirtyvät Nordea-tilillemme viitteen kanssa. Jätä maksun viestikenttä tyhjäksi.

Tilinumero: {iban}
Viitenumerosi: {ref}
Maksun summa: {sum}EUR
Eräpäivä: {duedate}

----

This is the payment information for your membership at Helsinki Hacklab ry for year {year}. Keyholder membership fees are handled
separately, with different reference.

Virtual barcode (usable with Finnish banks) contains all the information required to make a payment and is
the recommended way to input the payment information. Remember to make the payment repeat monthly. In the netbank there
should be "read barcode" (or similar) button, copy the barcode numbers to the dialog given after clicking the button.

Virtual barcode:
{barcode}

Manual input of payment information.

Pay extra attention when entering payment info manually. Helsinki Hacklab has two different accounts, please make sure
you are using our Nordea account and the correct reference number. Leave the message-field empty.

Account number: {iban}
Reference number: {ref}
Amount due: {sum}EUR
Due date: {duedate}

        """.format(
            iban=iban,
            ref=t.reference,
            sum=-t.amount,
            barcode=bank_barcode(iban, t.reference, -t.amount),
            year=year,
            duedate=duedate.date().isoformat()
        )
        try:
            mail.send()
        except Exception as e:
            logger.exception("Failed to send payment-info email to {}".format(member.email))

        return True


class TransactionHandler(BaseTransactionHandler):

    def __init__(self, *args, **kwargs):
        # We have to do this late to avoid problems with circular imports
        from members.models import Member
        self.memberclass = Member
        self.try_methods = [
            self.import_generic_transaction,
            self.import_legacy_transaction,
            self.import_holvi_overpaid_transaction,
        ]
        super().__init__(*args, **kwargs)

    def import_transaction(self, at):
        # We only care about transactions with reference numbers
        if not at.reference:
            return None

        # If local transaction exists, return as-is
        lt = at.get_local()
        if lt.pk:
            return lt

        # We have few importers to try
        for m in self.try_methods:
            new_lt = m(at, lt)
            if new_lt is not None:
                return new_lt
        # Nothing worked, return None
        return None

    def import_generic_transaction(self, at, lt):
        """Look for a transaction with same reference but oppsite value. If found use that for owner and tag"""
        qs = Transaction.objects.filter(reference=at.reference, amount=-at.amount).order_by('-stamp')
        if not qs.count():
            return None
        base = qs[0]
        lt.tag = base.tag
        lt.owner = base.owner
        lt.save()
        return lt

    def import_holvi_overpaid_transaction(self, at, lt):
        """Look for a transaction with same reference but opposite same or lesser value. If found use that for owner and tag"""
        if len(at.reference) < 2:  # Jus so we do not get indexerrors from empty references or something
            return None
        if at.reference[0:2] != "RF":
            return None
        qs = Transaction.objects.filter(reference=at.reference, amount__gte=-at.amount).order_by('-stamp')
        if not qs.count():
            return None
        base = qs[0]
        lt.tag = base.tag
        lt.owner = base.owner
        lt.save()
        if base.amount >= -at.amount:
            base.amount = -at.amount
            base.save()
        return lt

    def import_legacy_transaction(self, at, lt):
        """Look at the reference number and use it to find owner and tag if it matches our old reference format"""
        # Last meaningful number (last number is checksum) of the reference is used to recognize the TransactionTag
        if len(at.reference) < 2:  # Jus so we do not get indexerrors from empty references or something
            return None
        if at.reference[0:2] == "RF":
            return None
        try:
            lt.tag = TransactionTag.objects.get(tmatch=at.reference[-2])
        except TransactionTag.DoesNotExist:
            # No tag matched, skip...
            return None
        # Numbers up to the tag identifier in the reference is the member_id + 1000
        try:
            lt.owner = self.memberclass.objects.get(member_id=(int(at.reference[0:-2], 10) - 1000))
        except self.memberclass.DoesNotExist:
            # No member matched, skip...
            return None

        # Rest of the fields are directly mapped already by get_local()
        lt.save()
        return lt

    def __str__(self):
        return str(_("Helsinki Hacklab ry transactions handler"))
