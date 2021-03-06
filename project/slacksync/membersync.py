# -*- coding: utf-8 -*-
import collections
import logging
import time

import slacker
from django.conf import settings
from django.core.mail import send_mail
from members.models import Member
from requests.exceptions import RequestException
from requests.sessions import Session

from .utils import api_configured, get_client

logger = logging.getLogger()


class SlackMemberSync(object):
    """Sync members and slack members"""

    def get_slack_users_simple(self, slack, exclude_api_user=True):
        """Get just the properties we need from the slack members list"""
        response = slack.users.list()
        emails = []
        for member in response.body['members']:
            if 'email' not in member['profile']:
                # bot or similar
                continue
            if exclude_api_user and member['name'] == settings.SLACK_API_USERNAME:
                continue
            emails.append((member['id'], member['name'], member['profile']['email']))
        return emails

    def email_slack_link(self, member):
        send_mail(
            "Slack invite to {}".format(settings.ORGANIZATION_NAME),
            "Click on the link to continue\n\n{}\n".format(settings.SLACK_INVITE_LINK),
            settings.DEFAULT_FROM_EMAIL,
            [member.email],
            fail_silently=True
        )

    def sync_members(self, autodeactivate=False, resend=True):
        """Sync members, NOTE: https://github.com/ErikKalkoken/slackApiDoc/blob/master/users.admin.setInactive.md says
        deactivation via API works only on paid tiers"""
        if not api_configured():
            raise RuntimeError("Slack API not configured")
        with Session() as session:
            slack = get_client(session=session)
            slack_users = self.get_slack_users_simple(slack)
            slack_emails = set([x[2] for x in slack_users])
            add_members = collections.deque(Member.objects.exclude(email__in=slack_emails))

            while add_members:
                member = add_members.popleft()
                # If we have configured invite-link use it instead of API (which might hit the "too many invites" -error
                if settings.SLACK_INVITE_LINK:
                    self.email_slack_link(member)
                    continue
                try:
                    resp = slack.users.admin.invite(member.email, resend=resend)
                    if 'ok' not in resp.body or not resp.body['ok']:
                        self.logger.error("Could not invite {}, response: {}".format(member.email, resp.body))
                    time.sleep(0.25)  # rate-limit
                except slacker.Error as e:
                    if str(e) == 'sent_recently':
                        continue
                    if str(e) == 'invalid_email':
                        logger.error("Slack says {} is invalid_email".format(member.email))
                        continue
                    raise e
                except RequestException as e:
                    if 'Retry-After' in e.response.headers:
                        wait_s = int(e.response.headers['Retry-After'])
                        logger.warning("Asked to wait {}s before retrying invite for {}".format(wait_s, member.email))
                        time.sleep(wait_s * 1.5)
                        add_members.append(member)
                        continue
                    else:
                        logger.exception("Got exception when trying to invite {}".format(member.email))
                        raise e

            member_emails = set(Member.objects.values_list('email', flat=True))
            remove_slack_emails = slack_emails - member_emails
            remove_usernames = []
            if not remove_slack_emails:
                return remove_usernames

            usernames_by_email = {x[2]: x[1] for x in slack_users}
            remove_usernames = [(usernames_by_email[x], x) for x in remove_slack_emails]

            if not autodeactivate:
                return remove_usernames

            userids_by_email = {x[2]: x[0] for x in slack_users}
            remove_iter = collections.deque(remove_slack_emails)
            while remove_iter:
                email = remove_iter.popleft()
                try:
                    resp = slack.users.admin.setInactive(userids_by_email[email])
                    if 'ok' not in resp.body or not resp.body['ok']:
                        self.logger.error("Could not deactivate {}, response: {}".format(email, resp.body))
                    time.sleep(0.25)  # rate-limit
                except RequestException as e:
                    if 'Retry-After' in e.response.headers:
                        wait_s = int(e.response.headers['Retry-After'])
                        logger.warning("Asked to wait {}s before retrying deactivation for {}".format(wait_s, email))
                        time.sleep(wait_s * 1.5)
                        remove_iter.append(email)
                        continue
                    else:
                        logger.exception("Got exception when trying to deactivate {}".format(email))
                        raise e

            return remove_usernames
