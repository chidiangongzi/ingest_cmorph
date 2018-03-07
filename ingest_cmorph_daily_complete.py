import argparse
import calendar
from datetime import datetime
import gzip
import logging
import netCDF4
import numpy as np
import os
import shutil
import urllib.request
import warnings
import bz2

#-----------------------------------------------------------------------------------------------------------------------
# set up a basic, global _logger which will write to the console as standard error
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%Y-%m-%d  %H:%M:%S')
_logger = logging.getLogger(__name__)

#-----------------------------------------------------------------------------------------------------------------------
# ignore warnings
warnings.simplefilter('ignore', Warning)

#-----------------------------------------------------------------------------------------------------------------------
# days of each calendar month, for non-leap and leap years
_MONTH_DAYS_NONLEAP = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_MONTH_DAYS_LEAP = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

#-----------------------------------------------------------------------------------------------------------------------
def _read_daily_cmorph_to_monthly_sum(cmorph_dir,
                                      data_desc,
                                      data_year,
                                      data_month):
    
    # for each file in the data directory read the data and add to the cumulative
    summed_data = np.zeros((data_desc['xdef_count'] * data_desc['ydef_count'], ))
    for cmorph_file in os.listdir(cmorph_dir):
        
        # read the year and month from the file name, make sure they all match
        file_year = int(cmorph_file[-8:-4])
        file_month = int(cmorph_file[-4:-2])
        if file_year != data_year:
            continue
        elif file_month != data_month:
            continue

        # read the daily binary data from file, and byte swap if not little endian
        data = np.fromfile(os.sep.join((cmorph_dir, cmorph_file)), 'f')
        if not data_desc['little_endian']:
            data = data.byteswap()
            
        # convert missing values to zero, then if missing it's not actually added when we do the summation
        if data_desc['undef'] < 0:     # we assume the missing value is -999.0 or something like that (negative), check here
            data[data < 0] = 0.0
        
        # add to the summation array
        summed_data += data

    return summed_data

#-----------------------------------------------------------------------------------------------------------------------
def _get_years():
    
    return list(range(1998, 2018))  # we know this, but not portable/reusable

#FIXME use the below once we work out the proxy issue on Windows
#
#     # read the listing of directories from the list of raw data years, these should all be 4-digit years
#     f = ftplib.FTP()
#     f.connect('ftp://filsrv.cicsnc.org')
#     f.login('anonymous')
#     f.cwd('olivier/data_CMORPH_NIDIS/02_RAW')
#     ls = f.mlsd()
#     f.close()
# 
#     years = []
#     for items in ls:
#         if item['type'] == 'dir':
#             year = item['name']
#             if year.isdigit() and len(year) == 4 and int(year) > 1900:
#                 years.append(year)
#             
#     return years

#-----------------------------------------------------------------------------------------------------------------------
def _download_data_descriptor(data_descriptor_file):
    
    file_url = "ftp://filsrv.cicsnc.org/olivier/data_CMORPH_NIDIS/03_PGMS/CMORPH_V1.0_RAW_0.25deg-DLY_00Z.ctl"
    urllib.request.urlretrieve(file_url, data_descriptor_file)
    
#-----------------------------------------------------------------------------------------------------------------------
def _download_daily_files(destination_dir,
                          year, 
                          month,
                          obs_type='raw'):
    """
    :param destination_dir:
    :param year:
    :param month: 1 == January, ..., 12 == December   
    """

    # determine which set of days per month we'll use based on if leap year or not    
    if calendar.isleap(year):
        days_in_month = _MONTH_DAYS_LEAP
    else:
        days_in_month = _MONTH_DAYS_NONLEAP
        
    # the base URL we'll append to in order to get the individual file URLs
    year_month = str(year) + str(month).zfill(2)
    url_base = 'ftp://filsrv.cicsnc.org/olivier/data_CMORPH_NIDIS/'
    if obs_type == 'raw':
        url_base += '02_RAW/' + str(year) + '/' + year_month
    else:
        url_base += '01_GAUGE_ADJUSTED/' + str(year) + '/' + year_month
        
    # list of files we'll return
    files = []
    
    for day in range(days_in_month[month - 1]):
        
        # build the file name, URL, and local file name
        year_month_day = year_month + str(day + 1).zfill(2)
        if obs_type == 'raw':
            filename_unzipped = 'CMORPH_V1.0_RAW_0.25deg-DLY_00Z_' + year_month_day
        else:   # guage adjusted
            filename_unzipped = 'CMORPH_V1.0_ADJ_0.25deg-DLY_00Z_' + year_month_day
        zip_extension = '.bz2'
        if obs_type == 'raw' and year < 2004:   # the raw files use GZIP through 2003
            zip_extension = '.gz'
        filename_zipped = filename_unzipped + zip_extension
        
        file_url  = url_base + '/' + filename_zipped
        local_filename_zipped = destination_dir + '/' + filename_zipped
        local_filename_unzipped = destination_dir + '/' + filename_unzipped
        
        _logger.info('Downloading %s', file_url)
        
        # download the zipped file
        urllib.request.urlretrieve(file_url, local_filename_zipped)

        # decompress the zipped file
        if (year >= 2004) or (obs_type == 'adjusted'):
            # use BZ2 decompression for files after 2003
            with bz2.open(local_filename_zipped, 'r') as f_in, open(local_filename_unzipped, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        else:
            # use GZIP decompression for files before 2004
            with gzip.open(local_filename_zipped, 'r') as f_in, open(local_filename_unzipped, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
  
        # append to our list of data files
        files.append(local_filename_unzipped)
        
        # clean up the downloaded zip file
        os.remove(local_filename_zipped)
        
    return files

#-----------------------------------------------------------------------------------------------------------------------
def _compute_days(year_initial,
                  year_final,
                  year_since=1900):
    '''
    Computes the "number of days" equivalent for regular, incremental daily time steps given an initial year.
    Useful when using "days since <year_since>" as time units within a NetCDF dataset.
    
    :param year_initial: the initial year from which the day values should start, i.e. the first value in the output
                        array will correspond to the number of days between January 1st of this initial year and January 
                        1st of the units since year
    :param year_final: the final year through which the result values are computed
    :param year_since: the start year from which the day values are incremented, with result time steps measured
                             in days since January 1st of this year 
    :return: an array of time step increments, measured in days since midnight of January 1st of the units "since year"
    :rtype: ndarray of ints 
    '''
    
    # arguments validation
    if year_initial < year_since:
        raise ValueError('Invalid year arguments, initial data year is before the units since year, which is verboten')

    # total days between Jan. 1st of the initial data year and Dec. 31st of the final data year
    data_day_initial = datetime(year_initial, 1, 1).day
    total_days = (datetime(year_final, 12, 31) - data_day_initial).days
    
    # compute an offset from which the day values should begin 
    units_day_start = datetime(year_since, 1, 1)

    # initialize the list of day values we'll build
    days = np.empty(total_days, dtype=int)
    
    # loop over all day time steps, assigning into the days array at each step
    for i in range(total_days):
        days[i] = units_day_start + i
    
    return days

#-----------------------------------------------------------------------------------------------------------------------
def ingest_cmorph_to_netcdf(cmorph_dir,
                            descriptor_file,
                            netcdf_file,
                                 data_descriptor_file_name='CMORPH_V1.0_RAW_0.25deg-DLY_00Z.ctl',
                                 obs_type='raw',
                                 download_files=True,
                                 remove_files=True):
    """
    Ingests CMORPH daily precipitation files into a full period of record file containing daily precipitation values.
    
    :param cmorph_dir: work directory where CMORPH files are expected to be located, downloaded files will reside here
    :param netcdf_file: output NetCDF
    :param data_descriptor_file_name: file name of the data descriptor file in CMORPH directory
    :param download_files: if true then download data descriptor and data files from FTP, overwrites files in CMORPH work directory
    :param remove_files: if files were downloaded then remove them once operations have completed 
    """
    
    # download the descriptor file, if required
    descriptor_file = os.sep.join((cmorph_dir, 'data_descriptor.txt'))
    if download_files:
        _download_data_descriptor(descriptor_file)

    # read data description info
    data_desc = _read_description(descriptor_file)
    
    # clean up the file, if required
    if download_files and remove_files:
        os.remove(descriptor_file)
    
    # get the years covered
    years = _get_years()
        
    # create a corresponding NetCDF
    with netCDF4.Dataset(netcdf_file, 'w') as output_dataset:
        
        # create the time, x, and y dimensions
        output_dataset.createDimension('time', None)
        output_dataset.createDimension('lon', data_desc['xdef_count'])
        output_dataset.createDimension('lat', data_desc['ydef_count'])
    
        output_dataset.title = data_desc['title']
        
        # create the coordinate variables
        time_variable = output_dataset.createVariable('time', 'i4', ('time',))
        x_variable = output_dataset.createVariable('lon', 'f4', ('lon',))
        y_variable = output_dataset.createVariable('lat', 'f4', ('lat',))
        
        # set the coordinate variables' attributes
        units_since_year = 1800
        time_variable.units = 'days since %s-01-01 00:00:00' % units_since_year
        x_variable.units = 'degrees east'
        y_variable.units = 'degrees north'
        
        # compute the time values 
        time_variable[:] = _compute_days(data_desc['start_date'].year,
                                         len(years) * 366,    # 366 days per year, including a placeholder for Feb 29th 
                                         initial_month=data_desc['start_date'].month,
                                         units_start_year=units_since_year)
        
        # generate longitude and latitude values, assign these to the NetCDF coordinate variables
        lon_values = list(_frange(data_desc['xdef_start'], data_desc['xdef_start'] + (data_desc['xdef_count'] * data_desc['xdef_increment']), data_desc['xdef_increment']))
        lat_values = list(_frange(data_desc['ydef_start'], data_desc['ydef_start'] + (data_desc['ydef_count'] * data_desc['ydef_increment']), data_desc['ydef_increment']))
        x_variable[:] = np.array(lon_values, 'f4')
        y_variable[:] = np.array(lat_values, 'f4')
    
        # read the variable data from the CMORPH file, mask and reshape accordingly, and then assign into the variable
        data_variable = output_dataset.createVariable('prcp', 
                                                      'f8', 
                                                      ('time', 'lat', 'lon',), 
                                                      fill_value=data_desc['undef'])
        data_variable.units = 'mm'
        data_variable.standard_name = 'precipitation'
        data_variable.long_name = 'precipitation, monthly cumulative'
        data_variable.description = data_desc['title']

        # loop over each year/month, reading binary data from CMORPH files and adding into the NetCDF variable
        for year in years:
            for month in range(1, 13):

                # get the files for the month
                if download_files:
                    downloaded_files = _download_daily_files(cmorph_dir, year, month, obs_type)
                       
                # read all the data for the month as a sum from the daily values, assign into the appropriate slice of the variable
                data = _read_daily_cmorph_to_monthly_sum(cmorph_dir, data_desc, year, month)
                
                # assume values are in lat/lon orientation
                data = np.reshape(data, (1, data_desc['ydef_count'], data_desc['xdef_count']))

                # get the time index, which is actually the month's count from the start of the period of record                
                time_index = ((year - data_desc['start_date'].year) * 12) + month - 1
                
                # assign into the appropriate slice for the monthly time step
                data_variable[time_index, :, :] = data
        
                # clean up, if necessary
                if download_files and remove_files:
                    for file in downloaded_files:
                        os.remove(file)
                    
#-----------------------------------------------------------------------------------------------------------------------
def _frange(start, stop, step):
    i = start
    while i < stop:
        yield i
        i += step

#-----------------------------------------------------------------------------------------------------------------------
def _read_description(descriptor_file):
    """
    Reads a data descriptor file, example below:
    
        DSET ../0.25deg-DLY_00Z/%y4/%y4%m2/CMORPH_V1.0_RAW_0.25deg-DLY_00Z_%y4%m2%d2  
        TITLE  CMORPH Version 1.0BETA Version, daily precip from 00Z-24Z 
        OPTIONS template little_endian
        UNDEF  -999.0
        XDEF 1440 LINEAR    0.125  0.25
        YDEF  480 LINEAR  -59.875  0.25
        ZDEF   01 LEVELS 1
        TDEF 99999 LINEAR  01jan1998 1dy 
        VARS 1
        cmorph   1   99 yyyyy CMORPH Version 1.o daily precipitation (mm)  
        ENDVARS
        
    :param descriptor_file: ASCII file with data description information
    :return: dictionary of data description keys/values
    """
    
    data_dict = {}    
    with open(descriptor_file, 'r') as fp:
        for line in fp:
            words = line.split()
            if words[0] == 'UNDEF':
                data_dict['undef'] = float(words[1])
            elif words[0] == 'XDEF':
                data_dict['xdef_count'] = int(words[1])
                data_dict['xdef_start'] = float(words[3])
                data_dict['xdef_increment'] = float(words[4])
            elif words[0] == 'YDEF':
                data_dict['ydef_count'] = int(words[1])
                data_dict['ydef_start'] = float(words[3])
                data_dict['ydef_increment'] = float(words[4])
            elif words[0] == 'TDEF':
                data_dict['start_date'] = datetime.strptime(words[3], '%d%b%Y')  # example: "01jan1998"
            elif words[0] == 'OPTIONS':
                if words[2] == 'big_endian':
                    data_dict['little_endian'] = False
                else:   # assume words[2] == 'little_endian'
                    data_dict['little_endian'] = True
            elif words[0] == 'cmorph':  # looking for a line like this: "cmorph   1   99 yyyyy CMORPH Version 1.o daily precipitation (mm)"
                data_dict['variable_description'] = ' '.join(words[4:])
            elif words[0] == 'TITLE':
                data_dict['title'] = ' '.join(words[1:])

    return data_dict

#-----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    """
    This module is used to perform ingest of binary CMORPH datasets to NetCDF.

    Example command line usage for reading all daily files for all months into a single NetCDF file with cumulative 
    monthly precipitation for the full period of record (all months), with all files downloaded from FTP:
    
    $ python -u ingest_cmorph.py --cmorph_dir C:/home/data/cmorph/raw \
                                 --out_file C:/home/data/cmorph_file.nc \
                                 --download_files True
                                 
    """

    try:

        # log some timing info, used later for elapsed time
        start_datetime = datetime.now()
        _logger.info("Start time:    %s", start_datetime)

        # parse the command line arguments
        parser = argparse.ArgumentParser()
        parser.add_argument("--cmorph_dir", 
                            help="Directory containing daily binary CMORPH data files for a single month", 
                            required=True)
        parser.add_argument("--out_file", 
                            help="NetCDF output file containing variables read from the input data", 
                            required=True)
        parser.add_argument("--download_files", 
                            help="Download data from FTP, saving files in the CMORPH data directory specified by --cmorph_dir",
                            type=bool,
                            default=False, 
                            required=False)
        parser.add_argument("--remove_files", 
                            help="Remove downloaded data files from the CMORPH data directory if specified by --download_files",
                            type=bool,
                            default=False, 
                            required=False)
        parser.add_argument("--obs_type", 
                            help="Observation type, either raw or guage adjusted",
                            choices=['raw', 'adjusted'], 
                            default='raw',
                            required=False)
        args = parser.parse_args()

        print('\nIngesting CMORPH precipitation dataset')
        print('Result NetCDF:   %s' % args.out_file)
        print('Work directory:  %s' % args.cmorph_dir)
        print('\n\tDownloading files:   %s' % args.download_files)
        print('\tRemoving files:      %s' % args.remove_files)
        print('\tObservation type:    %s\n' % args.obs_type)
        
        # perform the ingest to NetCDF
#         ingest_cmorph_to_netcdf_daily(args.in_file, args.descriptor_file, args.out_file)
#         ingest_cmorph_to_netcdf_monthly(args.cmorph_dir, args.descriptor_file, args.out_file, year, month, args.download_files, args.remove_files)
        ingest_cmorph_to_netcdf_full(args.cmorph_dir,
                                     args.out_file,
#                                      data_descriptor_file_name=args.descriptor_file,
                                     obs_type=args.obs_type,
                                     download_files=args.download_files,
                                     remove_files=args.remove_files)
        # report on the elapsed time
        end_datetime = datetime.now()
        _logger.info("End time:      %s", end_datetime)
        elapsed = end_datetime - start_datetime
        _logger.info("Elapsed time:  %s", elapsed)

    except Exception as ex:
        _logger.exception('Failed to complete', exc_info=True)
        raise
    