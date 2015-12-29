from django.core.management.base import BaseCommand, CommandError
from ndaparser.importer import NDAImporter

class Command(BaseCommand):
    help = 'Imports Nordea transactions file'

    def add_arguments(self, parser):
        parser.add_argument('filepath')

    def handle(self, *args, **options):
        with open(options['filepath']) as fp:
            h = NDAImporter(fp)
            transactions = h.import_transactions()
            for t in transactions:
                print("Imported transaction %s" % t)
