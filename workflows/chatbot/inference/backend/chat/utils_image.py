import re
import exifread
import time
import requests
import base64
import datetime;
import random;
from io import BytesIO
from PIL import Image
from transformers import BlipProcessor, BlipForConditionalGeneration


def latitude_and_longitude_convert_to_decimal_system(*arg):
    """
    Convert latitude&longitude into decimal system
    """
    return float(arg[0]) + ((float(arg[1]) + (float(arg[2].split('/')[0]) / float(arg[2].split('/')[-1]) / 60)) / 60)


def find_GPS_image(pic_path):
    """
    generate GPS and timestamp from image
    """
    GPS = {}
    date = ''
    with open(pic_path, 'rb') as f:
        tags = exifread.process_file(f)
        print(f'====== image metadata ======')
        print(tags)
        for tag, value in tags.items():
            if re.match('GPS GPSLatitudeRef', tag):
                GPS['GPSLatitudeRef'] = str(value)
            elif re.match('GPS GPSLongitudeRef', tag):
                GPS['GPSLongitudeRef'] = str(value)
            elif re.match('GPS GPSAltitudeRef', tag):
                GPS['GPSAltitudeRef'] = str(value)
            elif re.match('GPS GPSLatitude', tag):
                try:
                    match_result = re.match('\[(\w*),(\w*),(\w.*)/(\w.*)\]', str(value)).groups()
                    GPS['GPSLatitude'] = int(match_result[0]), int(match_result[1]), int(match_result[2])
                except:
                    deg, min, sec = [x.replace(' ', '') for x in str(value)[1:-1].split(',')]
                    GPS['GPSLatitude'] = latitude_and_longitude_convert_to_decimal_system(deg, min, sec)
            elif re.match('GPS GPSLongitude', tag):
                try:
                    match_result = re.match('\[(\w*),(\w*),(\w.*)/(\w.*)\]', str(value)).groups()
                    GPS['GPSLongitude'] = int(match_result[0]), int(match_result[1]), int(match_result[2])
                except:
                    deg, min, sec = [x.replace(' ', '') for x in str(value)[1:-1].split(',')]
                    GPS['GPSLongitude'] = latitude_and_longitude_convert_to_decimal_system(deg, min, sec)
            elif re.match('GPS GPSAltitude', tag):
                GPS['GPSAltitude'] = str(value)
            elif re.match('Image DateTime', tag):
                date = str(value)
    return {'GPS_information': GPS, 'date_information': date}


def get_address_from_gps(latitude, longitude, api_key):
    base_url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        'latlng': f"{latitude},{longitude}",
        'key': api_key
    }
    proxies = {
        'http': 'child-prc.intel.com:913',
        'https': 'child-prc.intel.com:913',
    }
    start_time = time.time()
    response = requests.get(base_url, params=params, proxies=proxies)
    data = response.json()
    if data['status'] == 'OK':
        address = data['results'][0]['formatted_address']
        print("Generate address elapsed time: ", time.time() - start_time)
        return address
    else:
        return None
    

def infer_image(pic_path, processor, model):
    raw_image = Image.open(pic_path).convert('RGB')
    text = f"You take a photo of"
    inputs = processor(raw_image, text, return_tensors="pt")
    out = model.generate(**inputs, max_new_tokens=50)
    result_str = processor.decode(out[0], skip_special_tokens=True)
    return result_str


def generate_caption(img_path):
    processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-large")
    model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-large")
    start_time = time.time()
    result_str = infer_image(img_path, processor, model)
    print("Generate caption elapsed time: ", time.time() - start_time)
    return result_str


def image_to_byte64(img_path):
    image = Image.open(img_path)
    img_bytes = BytesIO()
    image = image.convert("RGB")
    image.save(img_bytes, format="JPEG")
    img_bytes = img_bytes.getvalue()
    img_b64 = base64.b64encode(img_bytes)
    return img_b64


def byte64_to_image(img_b64):
    img_bytes = base64.b64decode(img_b64)
    bytes_stream = BytesIO(img_bytes)
    img = Image.open(bytes_stream)
    return img

# UPDATEEEEEEEEEEEEEEEE
def generate_random_name():
    nowTime=datetime.datetime.now().strftime("%Y%m%dT%H%M%S%f")
    randomNum=random.randint(0,100)
    if randomNum<=10:
        randomNum=str(0)+str(randomNum)
    uniqueNum=str(nowTime)+str(randomNum)
    return uniqueNum


def transfer_xywh(facial_area: dict):
    items = ['x', 'y', 'w', 'h']
    result = ''
    for item in items:
        result += str(facial_area[item]) + '_'
    return result
