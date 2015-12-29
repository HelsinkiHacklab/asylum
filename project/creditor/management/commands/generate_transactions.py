# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand, CommandError
from creditor.tests.fixtures.transactions import TransactionFactory


class Command(BaseCommand):
    help = 'generate randomised transactions'

    def add_arguments(self, parser):
        parser.add_argument('amount', type=int)

    def handle(self, *args, **options):
        for i in range(options['amount']):
            trans = TransactionFactory()
            print("Generated transaction %s" % trans)
