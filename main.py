from scrapper import autoscout24_complete, autoscout24_recent, mobile_de_complete, mobile_de_recent, otomoto_complete, otomoto_recent
from database.db import ensure_database_exists
import sys

if __name__ == '__main__':
    arguments = sys.argv[1:]
    ensure_database_exists()
    if not arguments:
        arguments = ['otomoto_complete']
    if arguments[0] == 'autoscout24_complete':
        autoscout24_complete.main()
    elif arguments[0] == 'mobile_complete':
        mobile_de_complete.main()
    elif arguments[0] == 'autoscout24_recent':
        autoscout24_recent.main()
    elif arguments[0] == 'mobile_recent':
        mobile_de_recent.main()
    elif arguments[0] == 'otomoto_complete':
        otomoto_complete.main()
    elif arguments[0] == 'otomoto_recent':
        otomoto_recent.main()
    else:
        print('Available launcher names are: \n- autoscout24_complete\n- mobile_complete\n- autoscout24_recent\n- mobile_recent')
