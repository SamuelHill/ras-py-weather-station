#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Filename:    weather-station.py
# @Author:      Samuel Hill
# @Email:       whatinthesamhill@protonmail.com

"""Indoor weather station built for continuous operation on a Raspberry Pi 4.
    Uses a DHT-22, BMP-085, and light dependent resistor (LDR) to collect
    temperature, humidity, pressure, and light level data. Prints data to an
    LCD screen (with some custom characters for better stylization/display) and
    logs the data to an InfluxDB instance running locally on the pi."""

from time import sleep
from datetime import datetime, timedelta
from statistics import mean
from influxdb_client import InfluxDBClient, Point
from Adafruit_BMP import BMP085  # DEPRECATED, still works for now...
from adafruit_dht import DHT22
from board import MISO as DHT_PIN, D6 as LDR_PIN, D22 as NPN_PIN, \
                  D25 as PIN_RS, D24 as PIN_E, D26 as PIN_D4, \
                  D13 as PIN_D5, D12 as PIN_D6, D16 as PIN_D7
from gpiozero import LightSensor
from RPi.GPIO import BCM, cleanup, setup, OUT, HIGH, output
from RPLCD import CharLCD

REFRESH = 2.0
MSG_DURATION = 10.0
MSG_INTERVAL = timedelta(minutes=20)
LAST_MSG_TIME = datetime.now()

ldrSensor = LightSensor(LDR_PIN.id, charge_time_limit=0.1)
setup(NPN_PIN.id, OUT, initial=HIGH)  # DHT sensor is finicky, transistor fix
sleep(1.0)  # just giving it time to setup
dhtSensor = DHT22(DHT_PIN)
DHT_SUCCESS = True
bmpSensor = BMP085.BMP085()
KNOWN_ELEVATION = 185.6  # above sea level, in meters, where the station is
data = {'dht_temp': 0, 'humidity': 0, 'bmp_temp': 0,
        'pressure': 0, 'sea_level': 0, 'light_level': 0}
LATEST_PRESSURE = []
LATEST_SAMPLES = 5400  # 3h = 180m = 10800s / REFRESH = 5400

lcd = CharLCD(cols=20, rows=4, pin_rs=PIN_RS.id, pin_e=PIN_E.id,
              pins_data=[PIN_D4.id, PIN_D5.id, PIN_D6.id, PIN_D7.id],
              numbering_mode=BCM)
lcd.create_char(0, (0b01110, 0b01010, 0b01110, 0b00000,  # degree
                    0b00000, 0b00000, 0b00000, 0b00000))
lcd.create_char(1, (0b00000, 0b00100, 0b01010, 0b10001,  # water
                    0b10001, 0b10001, 0b01110, 0b00000))
lcd.create_char(2, (0b00000, 0b00100, 0b01110, 0b11011,  # sun
                    0b01110, 0b00100, 0b00000, 0b00000))
lcd.create_char(3, (0b00000, 0b10000, 0b01000, 0b00100,  # backslash
                    0b00010, 0b00001, 0b00000, 0b00000))
lcd.create_char(7, (0b00000, 0b01010, 0b10101, 0b10001,  # heart
                    0b01010, 0b00100, 0b00000, 0b00000))
lcd.clear()

URL = 'http://localhost:8086'
USERNAME, PASSWORD = 'admin', 'admin'
TOKEN = f'{USERNAME}:{PASSWORD}'
DATABASE, RETENTION_POLICY = 'homebridgeWeather', 'autogen'
BUCKET = f'{DATABASE}/{RETENTION_POLICY}'


def _write_new_line(row: int, to_write: str):
    lcd.cursor_pos = (row, 0)
    lcd.write_string(to_write)


def custom_message():
    """Checks if a MSG_INTERVAL duration of time has passed since LAST_MSG_TIME
        then posts a custom message for MSG_DURATION amount of time."""
    # need the globals to modify the properly scoped variables
    global LAST_MSG_TIME  # pylint: disable=global-statement
    if datetime.now() - LAST_MSG_TIME > MSG_INTERVAL:
        lcd.clear()
        _write_new_line(1, '     I love you')
        _write_new_line(2, '      Monica \x07')
        LAST_MSG_TIME = datetime.now()
        sleep(MSG_DURATION)


# pylint: disable=inconsistent-return-statements
def update_data():
    """Attempts to collect data from the DHT-22 sensor (toggling power if the
        read fails). Also collects data from both the BMP-085 sensor and the
        light dependent resistor (LDR). All of the collected data is stored in
        the data dictionary defined in this script (overwritten like a global).
        The pressure data is as added to a list of length LATEST_SAMPLES for
        later calculation of the pressure change.

    Returns:
        datetime: The datetime.now() call from the beginning of the update"""
    # need the globals to modify the properly scoped variables
    global DHT_SUCCESS, LATEST_PRESSURE  # pylint: disable=global-statement
    beginning_of_update = datetime.now()
    try:
        data['dht_temp'] = dhtSensor.temperature
        data['humidity'] = dhtSensor.humidity
    except RuntimeError as error:
        DHT_SUCCESS = False
        print(f'{datetime.now()} - {error.args[0]}')
        if 'check wiring' in error.args[0]:  # brute force and ignorance
            output(NPN_PIN.id, 0)  # turn it off
            sleep(1.0)             # and...
            output(NPN_PIN.id, 1)  # back on again
        return  # no endless loop waiting for data/sensor to powers up
    DHT_SUCCESS = True
    data['light_level'] = ldrSensor.value * 100
    data['bmp_temp'] = bmpSensor.read_temperature()
    data['pressure'] = bmpSensor.read_pressure()
    data['sea_level'] = int(bmpSensor.read_sealevel_pressure(KNOWN_ELEVATION))
    if len(LATEST_PRESSURE) > LATEST_SAMPLES:
        LATEST_PRESSURE = LATEST_PRESSURE[1:]
    LATEST_PRESSURE.append(data['sea_level'])
    return beginning_of_update


def store_data(database, time):
    """Takes the recently updated data dictionary from update_data and logs
    everything into the local InfluxDB instance.

    Args:
        database (InfluxDBClient): the client you are using to write to the db
        time (datetime): the time the new data was collected
    """
    def send_to_influx(ptype, tag, field, pdata):
        point = Point(ptype).tag('source', tag).field(field, pdata).time(time)
        database.write(bucket=BUCKET, record=point)

    send_to_influx('pressure', 'bmp', 'kPa', data['pressure'])
    send_to_influx('pressure', 'sea', 'kPa', data['sea_level'])
    if DHT_SUCCESS:
        send_to_influx('humidity', 'dht', 'percent', data['humidity'])
        send_to_influx('temperature', 'dht', 'celsius', data['dht_temp'])
    send_to_influx('temperature', 'bmp', 'celsius', data['bmp_temp'])
    send_to_influx('light_level', 'ldr', 'percent', data['light_level'])


def print_data_to_screen():
    """Prints the collected data out to the LCD screen"""
    def percent(value):
        return f'{value:.1f}%' if value < 100.0 else '100%'

    def temps(celsius):
        return f'{celsius:.1f}/{(celsius * (9 / 5) + 32):.0f}\x00C/F'

    direction = '-'
    if len(LATEST_PRESSURE) > 60:
        p_diff = mean(LATEST_PRESSURE[-30:]) - mean(LATEST_PRESSURE[:30])
        direction = '^^' if p_diff >= 500 else '^' if p_diff >= 250 else '/' \
            if p_diff >= 25 else '-' if p_diff >= -25 else '\x03' \
            if p_diff >= -250 else 'v' if p_diff > -500 else 'vv'

    lcd.clear()
    _write_new_line(0, f'{percent(data["humidity"])} \x01, '
                       f'{temps(data["dht_temp"])}')
    _write_new_line(1, f'{percent(data["light_level"])} \x02, '
                       f'{temps(data["bmp_temp"])}')
    _write_new_line(2, f'Actual: {data["pressure"] / 1000:.3f} kPa')
    _write_new_line(3, f'Sea: {data["sea_level"] / 1000:.3f} kPa {direction}')


if __name__ == '__main__':
    with InfluxDBClient(url=URL, token=TOKEN, org='-').write_api() as local_db:
        while True:
            custom_message()
            try:
                sample_time = update_data()
            except Exception as error:
                ldrSensor.close()
                dhtSensor.exit()
                lcd.close()
                cleanup()
                raise error
            store_data(local_db, sample_time)
            print_data_to_screen()
            sleep(REFRESH)
