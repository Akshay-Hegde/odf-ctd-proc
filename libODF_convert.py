import sys
import csv
import pandas as pd
import libODF_sbe_reader as sbe_rd
import libODF_sbe_equations_dict as sbe_eq

DEBUG = False

#lookup table for sensor data
###DOUBLE CHECK TYPE IS CORRECT###
short_lookup = {
    '55':{'short_name': 't', 'long_name':'SBE 3+ Temperature', 'units': 'C', 'type': 'float64'},
    '45':{'short_name': 'p', 'long_name':'SBE 9+ Pressure', 'units': 'dbar', 'type': 'float64'},
    '3':{'short_name': 'c', 'long_name':'SBE 4 Conductivity', 'units': 'S/m', 'type':'float64'},
    '38':{'short_name': 'o', 'long_name':'SBE 43 Oxygen', 'units': 'ml/l', 'type':'float64'},
    '11':{'short_name': 'fluoro', 'long_name':'Seapoint Fluorometer', 'units': 'ug/l', 'type':'float64'},
    '27':{'short_name': 'empty', 'long_name':'empty', 'units':'NA', 'type':'NA'},
    '0':{'short_name': 'alti', 'long_name':'Altitude', 'units':'m', 'type':'float64'},
    '71':{'short_name': 'cstar', 'long_name':'CStar', 'units': 'ug/l', 'type':'float64'},
    '61':{'short_name': 'u_def', 'long_name':'user defined', 'units':'V', 'type':'float64'},
    '1000':{'short_name': 'sal', 'long_name':'Salinity (C1 T1)', 'units':'PSU', 'type':'float64'}
}


def debugPrint(*args, **kwargs):
    if DEBUG:
        errPrint(*args, **kwargs)


def errPrint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def convertFromFiles(hex_file, xmlcon_file, debug=False):
    """Handler to convert engineering data to sci units automatically.
    Takes the full path and filename of the .hex and .XMLCON as arguments.
    Optionally takes a boolean debug flag to specify whether or not to display
    verbose messages to stderr
    """
    global DEBUG
    DEBUG = debug

    sbeReader = sbe_rd.SBEReader.from_paths(hex_file, xmlcon_file)

    return convertFromSBEReader(sbeReader, DEBUG)


def convertFromSBEReader(sbeReader, debug=False):
    """Handler to convert engineering data to sci units automatically.
    Takes SBEReader object that is already connected to the .hex and .XMLCON files.
    Optionally takes a boolean debug flag to specify whether or not to display
    verbose messages to stderr
    """

    global DEBUG
    DEBUG = debug

    # Retrieve parsed scans
    rawData = sbeReader.parsed_scans()

    # Convert raw data to dataframe
    raw_df = pd.DataFrame(rawData)
    raw_df.index.name = 'index'
    raw_df = raw_df.apply(pd.to_numeric, errors="ignore")

    #debugPrint("Raw Data Types:", raw_df.dtypes)
    #debugPrint("Raw Data:", raw_df.head)

    # Retrieve Config data
    rawConfig = sbeReader.parsed_config()

    #needs to search sensor dictionary, and compute in order:
    #temp, pressure, cond, salinity, oxygen, all aux.
    #run one loop that builds a queue to determine order of processing, must track which column to pull
    #process queue, store results in seperate arrays for reuse later
    #once queue is empty, attach results together according to format order or xmlcon order - structure to keep track
    queue_metadata = []
    results = {}
    temp_counter = 0
    cond_counter = 0
    oxygen_counter = 0
    u_def_counter = 0
    empty_counter = 0
    processed_data = []

    #Temporary arrays to hold sci_data in order to compute following sci_data (pressure, cond, temp, etc)
    t_array = []
    p_array = []
    c_array = []
    k_array = []

    ######
    # The following are definitions for every key in the dict below:
    #
    # sensor_id = number assigned by SBE for identification in XML
    # list_id = place in XML array by SBE for determining which sensor is which, alternatively channel number (freq+volt)
    # channel_pos = is it the first, second, third, etc sensor of its type in the data file, aux sensors default to 0
    # ranking = data processing ranking - temp first, then pressure, then conductivity, then oxygen, then aux
    # column = column in the raw_df containing the engineering units to be converted to sci units
    # sensor_info = xml sensor info to convert from eng units to sci units
    ######

    for i, x in enumerate(rawConfig['Sensors']):
        #print(i)
        #print(rawConfig['Sensors'][i])
        sensor_id = rawConfig['Sensors'][i]['SensorID']

        #temp block
        if sensor_id == '55':
            temp_counter += 1
            queue_metadata.append({'sensor_id': '55', 'list_id': i, 'channel_pos': temp_counter, 'ranking': 1, 'column': i, 'sensor_info':rawConfig['Sensors'][i]})

        #cond block
        elif str(sensor_id) == '3':
            cond_counter += 1
            queue_metadata.append({'sensor_id': '3', 'list_id': i, 'channel_pos': cond_counter, 'ranking': 3, 'column': i, 'sensor_info':rawConfig['Sensors'][i]})

        #pressure block
        elif str(sensor_id) == '45':
            queue_metadata.append({'sensor_id': '45', 'list_id': i, 'channel_pos': '', 'ranking': 2, 'column': i, 'sensor_info':rawConfig['Sensors'][i]})

        #oxygen block
        elif str(sensor_id) == '38':
            oxygen_counter += 1
            queue_metadata.append({'sensor_id': '38', 'list_id': i, 'channel_pos': oxygen_counter, 'ranking': 5, 'column': i, 'sensor_info':rawConfig['Sensors'][i]})

        #empty block
        elif str(sensor_id) == '27':
            empty_counter += 1
            queue_metadata.append({'sensor_id': '27', 'list_id': i, 'channel_pos': empty_counter, 'ranking': 6, 'column': i, 'sensor_info':rawConfig['Sensors'][i]})

        #u_def block
        elif str(sensor_id) == '61':
            u_def_counter += 1
            queue_metadata.append({'sensor_id': '61', 'list_id': i, 'channel_pos': u_def_counter, 'ranking': 6, 'column': i, 'sensor_info':rawConfig['Sensors'][i]})

        #aux block
        else:
            queue_metadata.append({'sensor_id': sensor_id, 'list_id': i, 'channel_pos': '', 'ranking': 7, 'column': i, 'sensor_info':rawConfig['Sensors'][i]})

    #a temporary block in order to append basic salinity (t1, c1) to file. If additional salinity is needed (different combinations), it'll need a full reworking
    queue_metadata.append({'sensor_id': '1000', 'list_id': 1000, 'channel_pos':'', 'ranking': 4, 'column': '', 'sensor_info':''})

    #queue sorting forces it to be in order, so we don't worry about order here
    #assumes first channel for each sensor is primary for computing following data, rework to accept file to determine which is primary
    queue_metadata = sorted(queue_metadata, key = lambda sensor: sensor['ranking'])
    #debugPrint("Queue Metadata:", json.dumps(queue_metadata, indent = 2))

    #empty converted dataframs
    converted_df = pd.DataFrame()

    for temp_meta in queue_metadata:

        column_name = '{0}{1}_{2}'.format(short_lookup[temp_meta['sensor_id']]['short_name'], temp_meta['channel_pos'], short_lookup[temp_meta['sensor_id']]['units'])

        ###Temperature block
        if temp_meta['sensor_id'] == '55':
            debugPrint('Processing Sensor ID:', temp_meta['sensor_id'] + ',', short_lookup[temp_meta['sensor_id']]['long_name'])
            converted_df[column_name] = sbe_eq.temp_its90_dict(temp_meta['sensor_info'], raw_df[temp_meta['column']])
            if temp_meta['list_id'] == 0:
                t_array = converted_df[column_name].astype(type('float', (float,), {}))
                k_array = [273.15+celcius for celcius in t_array]
                debugPrint('\tPrimary temperature used:', t_array[0], short_lookup[temp_meta['sensor_id']]['units'])
            #processed_data.append(temp_meta)

        ### Pressure block
        elif temp_meta['sensor_id'] == '45':
            debugPrint('Processing Sensor ID:', temp_meta['sensor_id'] + ',', short_lookup[temp_meta['sensor_id']]['long_name'])
            converted_df[column_name] = sbe_eq.pressure_dict(temp_meta['sensor_info'], raw_df[temp_meta['column']], t_array)
            if temp_meta['list_id'] == 2:
                p_array = converted_df[column_name].astype(type('float', (float,), {}))
                debugPrint('\tPressure used:', p_array[0], short_lookup[temp_meta['sensor_id']]['units'])
            #processed_data.append(temp_meta)

        ### Conductivity block
        elif temp_meta['sensor_id'] == '3':
            debugPrint('Processing Sensor ID:', temp_meta['sensor_id'] + ',', short_lookup[temp_meta['sensor_id']]['long_name'])
            converted_df[column_name] = sbe_eq.cond_dict(temp_meta['sensor_info'], raw_df[temp_meta['column']], t_array, p_array)
            if temp_meta['list_id'] == 1:
                c_array = converted_df[column_name].astype(type('float', (float,), {}))
                debugPrint('\tPrimary cond used:', c_array[0], short_lookup[temp_meta['sensor_id']]['units'])
            #processed_data.append(temp_meta)

        ### Oxygen block
        elif temp_meta['sensor_id'] == '38':
            debugPrint('Processing Sensor ID:', temp_meta['sensor_id'] + ',', short_lookup[temp_meta['sensor_id']]['long_name'])
            converted_df[column_name] = sbe_eq.oxy_dict(temp_meta['sensor_info'], p_array, k_array, t_array, c_array, raw_df[temp_meta['column']])
            #processed_data.append(temp_meta)

        ### Fluorometer Seapoint block
        elif temp_meta['sensor_id'] == '11':
            debugPrint('Processing Sensor ID:', temp_meta['sensor_id'] + ',', short_lookup[temp_meta['sensor_id']]['long_name'])
            converted_df[column_name] = sbe_eq.fluoro_seapoint_dict(temp_meta['sensor_info'], raw_df[temp_meta['column']])
            #processed_data.append(temp_meta)

        ###Salinity block
        elif temp_meta['sensor_id'] == '1000':
            debugPrint('Processing Sensor ID:', temp_meta['sensor_id'] + ',', short_lookup[temp_meta['sensor_id']]['long_name'])
            converted_df[column_name] = sbe_eq.sp_dict(c_array, t_array, p_array)
            #processed_data.append(temp_meta)

        ### Aux block
        else:
            debugPrint('Passing along Sensor ID:', temp_meta['sensor_id'] + ',', short_lookup[temp_meta['sensor_id']]['long_name'])
            converted_df[column_name] = raw_df[temp_meta['column']]
            #processed_data.append(temp_meta)

    # Set the column name for the index
    converted_df.index.name = 'index'

    # The meta data field needs to be processed seperately and then joined with the converted_df
    debugPrint("Building meta data dataframe... ", end='')
    metaArray = [line.split(',') for line in sbeReader._parse_scans_meta().tolist()]
    metaArrayheaders = sbeReader._breakdown_header()
    meta_df = pd.DataFrame(metaArray)

    meta_df.columns = metaArrayheaders[0]
    meta_df.index.name = 'index'

    for i, x in enumerate(metaArrayheaders[0]):
        #debugPrint('Set', metaArrayheaders[0][i], 'to', metaArrayheaders[1][i])
        if not metaArrayheaders[1][i] == 'bool_':
            meta_df[metaArrayheaders[0][i]] = meta_df[metaArrayheaders[0][i]].astype(metaArrayheaders[1][i])
        else:
            meta_df[metaArrayheaders[0][i]] = meta_df[metaArrayheaders[0][i]].str.match('True', na=False)
            #debugPrint(meta_df[metaArrayheaders[0][i]].head())

    debugPrint('Success!')

    debugPrint("Joining meta data dataframe with converted data... ", end='')
    converted_df = converted_df.join(meta_df)
    debugPrint('Success!')

    # return the converted data as a dataframe
    return converted_df


def importConvertedFile(fileName, debug=False):

    """Handler to import converted data from a csv-formatted file created by run.py
    """
    global DEBUG
    DEBUG = debug

    debugPrint("Importing data from:", fileName + '... ', end='')
    output_df = pd.read_csv(fileName, index_col=0, skiprows=[1], parse_dates=False)
    #debugPrint(output_df.head())
    header_raw = output_df.columns.values.tolist()
    header_type = []

    with open(fileName) as csvfile:
        dtypeReader = csv.reader(csvfile, delimiter=',')
        dtypeReader.__next__() # skip first row
        dtype_header = dtypeReader.__next__() #second row
        dtype_header.pop(0) #remove 'index' from left of dtype list
        #debugPrint(dtype_header)

    for i, x in enumerate(dtype_header):
        #debugPrint('Set', header_raw[i], 'to', dtype_header[i])
        if dtype_header[i] == 'bool_':
            d = {'True': True, 'False': False}
            output_df[header_raw[i]].map(d)
        elif dtype_header[i] == 'datetime_':
            output_df[header_raw[i]] = output_df[header_raw[i]].astype('datetime64')
        elif dtype_header[i] == 'int_':
            output_df[header_raw[i]] = output_df[header_raw[i]].astype('int64')
        elif dtype_header[i] == 'float_':
            output_df[header_raw[i]] = output_df[header_raw[i]].astype('float64')

    debugPrint("Done!")

    # return the imported data as a dataframe
    return output_df


def saveConvertedDataToFile(converted_df, filename, debug=False):

    # Save the bottle fire dataframe to file.
    column_names = ['index']
    column_names += converted_df.columns.tolist()
    #debugPrint("Column Names:", ','.join(column_names))

    datatype_names = ['index']
    for column in converted_df.columns:

        if converted_df[column].dtype.name == 'float64':
            datatype_names.append('float_')
        elif converted_df[column].dtype.name == 'datetime64[ns]':
            datatype_names.append('datetime_')
        elif converted_df[column].dtype.name == 'bool':
            datatype_names.append('bool_')
        elif converted_df[column].dtype.name == 'int64':
            datatype_names.append('int_')
        else:
            datatype_names.append(converted_df[column].dtype.name)
    #debugPrint("Datatypes Names:", ','.join(datatype_names))

    # write the header and dtype rows to file
    try:
        with open(filename, 'w') as f:
            f.write(','.join(column_names) + '\n')
            f.write(','.join(datatype_names) + '\n')
    except:
        errPrint('ERROR: Could not save bottle fire data header to file')
        return False
    else:
        debugPrint('Success!')

    # write the contents of the dataframe to file
    try:
        converted_df.to_csv(filename, mode='a', header=False)
    except:
        errPrint('ERROR: Could not save bottle fire data to file')
        return False
    else:
        debugPrint('Success!')

    return True

