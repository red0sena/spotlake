import time
import boto3
import pickle
import pandas as pd
from botocore.config import Config
from botocore.exceptions import ClientError


session = boto3.session.Session(region_name='us-west-2')
write_client = session.client('timestream-write', config=Config(read_timeout=20, max_pool_connections=5000, retries={'max_attempts':10}))

BUCKET_NAME = 'spotlake'
DATABASE_NAME = 'spotlake'
TABLE_NAME = 'azure'
FILENAME = 'latest_azure.json'
S3_PATH = f'latest_data/{FILENAME}'


# Submit Batch To Timestream
def submit_batch(records, counter, recursive):
    if recursive == 10:
        return
    try:
        result = write_client.write_records(DatabaseName=DATABASE_NAME, TableName = TABLE_NAME, Records=records, CommonAttributes={})
    except write_client.exceptions.RejectedRecordsException as err:
        print(err)
        re_records = []
        for rr in err.response["RejectedRecords"]:
            re_records.append(records[rr["RecordIndex"]])
        submit_batch(re_records, counter, recursive + 1)
        exit()
    except Exception as err:
        print(err)
        exit()


# Check Database And Table Are Exist and Upload Data to Timestream
def upload_timestream(data, timestamp):
    data = data[['InstanceTier', 'InstanceType', 'Region', 'OndemandPrice', 'SpotPrice']]

    time_value = time.strptime(timestamp.strftime("%Y-%m-%d %H:%M"), '%Y-%m-%d %H:%M')
    time_value = time.mktime(time_value)
    time_value = str(int(round(time_value * 1000)))

    records = []
    counter = 0
    for idx, row in data.iterrows():

        dimensions = []
        for column in ['InstanceTier', 'InstanceType', 'Region']:
            dimensions.append({'Name':column, 'Value': str(row[column])})

        submit_data = {
                'Dimensions': dimensions,
                'MeasureName': 'azure_values',
                'MeasureValues': [],
                'MeasureValueType': 'MULTI',
                'Time': time_value
        }
        
        for column, types in [('OndemandPrice', 'DOUBLE'), ('SpotPrice', 'DOUBLE')]:
            submit_data['MeasureValues'].append({'Name': column, 'Value': str(row[column]), 'Type' : types})
            
        records.append(submit_data)
        counter += 1
        if len(records) == 100:
            submit_batch(records, counter, 0)
            records = []

    if len(records) != 0:
        submit_batch(records, counter, 0)


def update_latest(data):
    result = data.to_json(FILENAME)
    session = boto3.Session()
    s3 = session.client('s3')
    with open(FILENAME, 'rb') as f:
        s3.upload_fileobj(f, BUCKET_NAME, S3_PATH)
    pickle.dump(data, open("./azure/latest_df.pkl", "wb"))

    
def save_raw(data, timestamp):
    data.to_csv(f"spotlake_{timestamp}", index=False, compression="gzip")
    session = boto3.Session()
    s3 = session.client('s3')
    s3_dir_name = timestamp.strftime("%Y/%m/%d")
    s3_obj_name = timestamp.strftime("%H:%M:%S")

    with open(SAVE_FILENAME, 'rb') as f:
        s3.upload_fileobj(f, BUCKET_NAME, f"rawdata/{s3_dir_name}/{s3_obj_name}.csv.gz")