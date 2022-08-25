from time import sleep
from datetime import datetime, timedelta
from statistics import mean
from board import MISO as DHT_PIN, D6 as LDR_PIN, D22 as NPN_PIN, \
                  D25 as PIN_RS, D24 as PIN_E, D26 as PIN_D4, \
                  D13 as PIN_D5, D12 as PIN_D6, D16 as PIN_D7
from RPi.GPIO import BCM, cleanup, setup, OUT, HIGH, output
from gpiozero import LightSensor
from adafruit_dht import DHT22
from Adafruit_BMP import BMP085
from RPLCD import CharLCD
from influxdb_client import InfluxDBClient, Point

# Timings:
REFRESH = 2.0
MSG_DURATION = 10.0
MSG_INTERVAL = timedelta(minutes=20)
LAST_MSG_TIME = datetime.now()

# Sensors:
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

# Out:
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

URL = 'http://localhost:8086'  # influx db
USERNAME, PASSWORD = 'admin', 'admin'
TOKEN = f'{USERNAME}:{PASSWORD}'
DATABASE, RETENTION_POLICY = 'homebridgeWeather', 'autogen'
BUCKET = f'{DATABASE}/{RETENTION_POLICY}'


def custom_message():
    global LAST_MSG_TIME
    if datetime.now() - LAST_MSG_TIME > MSG_INTERVAL:
        lcd.clear()
        lcd.cursor_pos = (1, 0)
        lcd.write_string('     I love you')
        lcd.cursor_pos = (2, 0)
        lcd.write_string('      Monica \x07')
        LAST_MSG_TIME = datetime.now()
        sleep(MSG_DURATION)


def update_data():
    global DHT_SUCCESS, LATEST_PRESSURE
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
    def percent(value):
        return f'{value:.1f}%' if value < 100.0 else '100%'

    def temps(celsius):
        return f'{celsius:.1f}/{(celsius * (9 / 5) + 32):.0f}\x00C/F'

    def direction():
        if len(LATEST_PRESSURE) > 60:
            p_diff = mean(LATEST_PRESSURE[-30:]) - mean(LATEST_PRESSURE[:30])
            return '^^' if p_diff >= 500 else '^' if p_diff >= 250 else '/' \
                if p_diff >= 25 else '-' if p_diff >= -25 else '\x03' \
                if p_diff >= -250 else 'v' if p_diff > -500 else 'vv'
        return '-'

    lcd.clear()
    lcd.cursor_pos = (0, 0)
    lcd.write_string(f'{percent(data["humidity"])} \x01, '
                     f'{temps(data["dht_temp"])}')
    lcd.cursor_pos = (1, 0)
    lcd.write_string(f'{percent(data["light_level"])} \x02, '
                     f'{temps(data["bmp_temp"])}')
    lcd.cursor_pos = (2, 0)
    lcd.write_string(f'Actual: {data["pressure"] / 1000:.3f} kPa')
    lcd.cursor_pos = (3, 0)
    lcd.write_string(f'Sea: {data["sea_level"] / 1000:.3f} kPa {direction()}')


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
