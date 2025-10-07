from scrapper import autoscout24_final, mobile_de_final, autoscout24_hourly, mobile_de_hourly
from utils import combined_data
from database.create_database import ensure_database_exists
import sys

if __name__ == '__main__':
    # arguments = sys.argv[1:]
    ensure_database_exists()
    arguments = ['mobile']
    if arguments[0] == 'autoscout24':
        autoscout24_final.main()
    elif arguments[0] == 'mobile':
        mobile_de_final.main()
    elif arguments[0] == 'autoscout24_hourly':
        autoscout24_hourly.main()
    elif arguments[0] == 'mobile_hourly':
        mobile_de_hourly.main()
    elif arguments[0] == 'combine':
        combined_data.combine_data()
    else:
        print('Available launcher names are: \n- autoscout24\n- mobile\n- combine')
