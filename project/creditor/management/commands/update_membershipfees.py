# -*- coding: utf-8 -*-
import datetime

import dateutil.parser
from creditor.models import RecurringTransaction, TransactionTag
from creditor.tests.fixtures.recurring import MembershipfeeFactory
from django.core.management.base import BaseCommand, CommandError
from members.models import Member


class Command(BaseCommand):
    help = 'Update membership fee RecurringTransactions'

    def add_arguments(self, parser):
        parser.add_argument('oldamount', type=int)
        parser.add_argument('cutoffdate', type=str)
        parser.add_argument('newamount', type=int)

    def handle(self, *args, **options):
        cutoff_dt = dateutil.parser.parse(options['cutoffdate'])
        end_dt = cutoff_dt - datetime.timedelta(minutes=1)

        # Some sanity checks
        if options['oldamount'] > 0:
            print("WARNING: You probably meant to use negative amount")
        if options['oldamount'] < 0 and options['newamount'] > 0:
            print("ERROR: Old amount is negative and new is positive, this cannot be right")
            return False
        if options['oldamount'] > 0 and options['newamount'] < 0:
            print("ERROR: Old amount is positive and new is negative, this cannot be right")
            return False

        tgt_tag = TransactionTag.objects.get(label='Membership fee', tmatch='1')
        for rt in RecurringTransaction.objects.filter(
            rtype=RecurringTransaction.YEARLY,
            tag=tgt_tag,
            end=None,
            start__lt=cutoff_dt,
            amount=options['oldamount']
        ):
            rt.end = end_dt
            rt.save()
            newrt = MembershipfeeFactory.create(amount=options['newamount'], start=cutoff_dt, end=None, owner=rt.owner)
            if options['verbosity'] > 0:
                print("Generated RecurringTransaction %s" % newrt)
        for rt in RecurringTransaction.objects.filter(
            rtype=RecurringTransaction.YEARLY,
            tag=tgt_tag,
            end=None,
            start=cutoff_dt,
            amount=options['oldamount']
        ):
            rt.amount = options['newamount']
            rt.save()
            if options['verbosity'] > 0:
                print("Updated RecurringTransaction %s" % rt)
