from scrapper import autoscout24_complete, autoscout24_recent, mobile_de_complete, mobile_de_recent
from database.create_database import ensure_database_exists
import sys

if __name__ == '__main__':
    arguments = sys.argv[1:]
    ensure_database_exists()
    # arguments = ['mobile']
    if arguments[0] == 'autoscout24':
        autoscout24_complete.main()
    elif arguments[0] == 'mobile':
        mobile_de_complete.main()
    elif arguments[0] == 'autoscout24_hourly':
        autoscout24_recent.main()
    elif arguments[0] == 'mobile_hourly':
        mobile_de_recent.main()
    else:
        print('Available launcher names are: \n- autoscout24\n- mobile\n- autoscout24_hourly\n- mobile_hourly')
