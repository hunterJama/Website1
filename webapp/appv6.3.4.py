from flask import Flask, render_template, jsonify, request
from oandapyV20 import endpoints
from oandapyV20.contrib.requests import MarketOrderRequest
from oandapyV20 import API
import oandapyV20.endpoints.forexlabs as labs
import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import pprint
import time
import oandapyV20.endpoints.orders as orders
import datetime
import requests
import json
from rq import Queue
from rq.job import Job
from rq.registry import StartedJobRegistry
from rq.worker import Worker
from multiprocessing import Process

app = Flask(__name__, static_folder="static")

# Define the interval in minutes
interval_minutes = 5

# Define your trading parameters
symbol = 'GBP_JPY'  # Example trading pair
timeframe_30min = 'M30'  # 30-minute timeframe
timeframe_15min = 'M15'  # 15-minute timeframe
risk_reward_ratio = 3  # Risk-to-reward ratio
stop_loss_range = (200, 300)  # Stop loss range in pips
entry = 0
orderID = ""
running = True
roof = 0
floor = 0
entry_details = {}
checkcount = 0

YOUR_ACCESS_TOKEN = "96703d46bc94f34789ad05a415f3eb8a-2529e14d54da8ed8a9a58a32bca2949a"
YOUR_ACCOUNT_ID = '101-001-26139829-003'

# Initialize the API client
api = API(access_token=YOUR_ACCESS_TOKEN)


# Function to get selected currency from frontend
@app.route("/confirm-currency")
def confirm_currency():
    global symbol
    # Retrieve the selected currency from the query parameters
    currency = request.args.get("currency")
    symbol = currency

    # Add your code here to process the selected currency
    # You can pass it to the TradeBot or perform any other operations

    return "Currency confirmed: " + currency


# Function to check if a candle is bullish
def is_bullish(candle):
    return float(candle['mid']['c']) > float(candle['mid']['o'])  # Close price > Open price


# Function to check if a candle is strong
def is_strong(candle):
    close_price = float(candle['mid']['c'])
    open_price = float(candle['mid']['o'])
    high_price = float(candle['mid']['h'])
    low_price = float(candle['mid']['l'])
    return (close_price - open_price) > (high_price - low_price) * 0.2


# Function to check if the price is above the old 30 minutes roof
def is_above_roof(candles):
    highs = [candle['mid']['c'] for candle in candles[:-1] if 'c' in candle['mid']]
    global roof
    if highs:
        roof = float(max(highs))
        print(roof, "roof ==>")
        return float(candles[-1]['mid']['c']) > roof  # Close price > Roof
    else:
        print('High value (h) not found in candles')
        return False


# Function to calculate the stop loss and take profit levels
def calculate_levels(entry_price):
    stop_loss = entry_price - stop_loss_range[0]  # Use the lower value of the stop loss range
    take_profit = entry_price + (stop_loss_range[1] * risk_reward_ratio)
    return stop_loss, take_profit


# Function to execute the buy trade
def execute_buy_trade(entry_price):
    stop_loss, take_profit = calculate_levels(entry_price)
    entry_price = round(entry_price, 3)
    stop_loss = round(stop_loss, 3)
    take_profit = round(take_profit, 3)

    actual_stop_loss = round(entry_price - (0.25), 3)
    actual_take_profit = round(entry_price + (0.75), 3)

    # Place your buy trade execution code here using the OANDA API
    data = {
        "order": {
            "price": str(entry_price),
            "stopLossOnFill": {
                "timeInForce": "GTC",
                "price": str(actual_stop_loss)
            },
            "takeProfitOnFill": {
                "timeInForce": "GTC",
                "price": str(actual_take_profit)
            },
            "timeInForce": "GTC",
            "instrument": symbol,
            "units": "10000",
            "type": "LIMIT",
            "positionFill": "DEFAULT"
        }
    }

    try:
        r = orders.OrderCreate(YOUR_ACCOUNT_ID, data=data)
        api.request(r)
        print('Executing buy trade')
        print('Entry Price:', entry_price)
        print('Stop Loss:', stop_loss, "==>", actual_stop_loss)
        print('Take Profit:', take_profit, "==>", actual_take_profit)

        entry_details['type'] = 'Buy'
        entry_details['entry_price'] = entry_price
        entry_details['stop_loss'] = stop_loss
        entry_details['take_profit'] = take_profit

        # Get the current time
        current_time = datetime.datetime.now()

        # Format the current time as a string
        current_time_string = current_time.strftime("%H:%M:%S")
        entry_time = current_time_string
        entry_details['entry_time'] = entry_time

        message = [
            "Currency: " + symbol,
            "Type: Sell",
            "Entry: " + str(entry_price),
            "Take Profit: " + str(take_profit),
            "Stop Loss: " + str(stop_loss),
            "Time: " + current_time_string
        ]
        send_message(bot_token, message)

    except oandapyV20.exceptions.V20Error as e:
        print(f"Error executing buy trade: {str(e)}")


def enter_buy():
    # Fetch historical candle data for the past 30 minutes
    global entry
    params = {
        'count': 12,  # Fetch 25 candles to ensure availability of the last 30 minutes
        'granularity': "M30"
    }
    r = instruments.InstrumentsCandles(instrument=symbol, params=params)

    try:
        candles_30min = api.request(r)['candles']

        # Check if the price is above the old 30 minutes roof
        if is_above_roof(candles_30min):
            # Check if the previous 30 minutes candle has closed
            if 'c' in candles_30min[-2]['mid']:
                # Fetch historical candle data for the past 15 minutes
                params1 = {
                    'count': 12,  # Fetch 16 candles to ensure availability of the last 15 minutes
                    'granularity': "M15"
                }
                r = instruments.InstrumentsCandles(instrument=symbol, params=params1)

                try:
                    candles_15min = api.request(r)['candles']

                    # Check if the last 15 minutes candle has closed
                    if 'c' in candles_15min[-1]['mid']:
                        # Check for a strong bullish candle on the 15-minute timeframe
                        if is_bullish(candles_15min[-1]):
                            entry_price = float(candles_15min[-1]['mid']['c'])  # Close price of the last candle
                            execute_buy_trade(entry_price)
                            entry = 1
                        else:
                            print('No strong bullish candle found on the 15-minute timeframe')
                    else:
                        print('Last 15 minutes candle has not closed yet')
                except oandapyV20.exceptions.V20Error as e:
                    print(f"Error fetching 15-minute candles: {str(e)}")
            else:
                print('Previous 30 minutes candle has not closed yet')
        else:
            print('Price is not above the old 30 minutes roof')
    except oandapyV20.exceptions.V20Error as e:
        print(f"Error fetching 30-minute candles: {str(e)}")


# Function to check if a candle is bearish
def is_bearish(candle):
    return float(candle['mid']['o']) > float(candle['mid']['c'])  # Open price > Close price


# Function to check if the price is below the old 30 minutes floor
def is_below_floor(candles):
    lows = [float(candle['mid']['c']) for candle in candles[:-1] if 'c' in candle['mid']]
    global floor
    if lows:
        floor = min(lows)
        print(floor, "floor ==>")
        return float(candles[-1]['mid']['c']) < floor  # Close price < Floor
    else:
        print('Low value (c) not found in candles')
        return False


# Function to execute the sell trade
def execute_sell_trade(entry_price):
    stop_loss, take_profit = calculate_levels(entry_price)
    entry_price = round(entry_price, 2)
    stop_loss = round(stop_loss, 2)
    take_profit = round(take_profit, 2)

    actual_stop_loss = round(entry_price + (0.25), 3)
    actual_take_profit = round(entry_price - (0.75), 3)

    # Place your sell trade execution code here using the OANDA API
    data = {
        "order": {
            "price": str(entry_price),
            "stopLossOnFill": {
                "timeInForce": "GTC",
                "price": str(actual_stop_loss)
            },
            "takeProfitOnFill": {
                "timeInForce": "GTC",
                "price": str(actual_take_profit)
            },
            "timeInForce": "GTC",
            "instrument": symbol,
            "units": "-10000",
            "type": "LIMIT",
            "positionFill": "DEFAULT"
        }
    }

    try:
        r = orders.OrderCreate(YOUR_ACCOUNT_ID, data=data)
        api.request(r)
        print('Executing sell trade')
        print('Entry Price:', entry_price)
        print('Stop Loss:', stop_loss, "==>", actual_stop_loss)
        print('Take Profit:', take_profit, "==>", actual_take_profit)

        entry_details['type'] = 'Sell'
        entry_details['entry_price'] = entry_price
        entry_details['stop_loss'] = stop_loss
        entry_details['take_profit'] = take_profit

        # Get the current time
        current_time = datetime.datetime.now()

        # Format the current time as a string
        current_time_string = current_time.strftime("%H:%M:%S")
        entry_time = current_time_string
        entry_details['entry_time'] = entry_time

        message = [
            "Currency: " + symbol,
            "Type: Sell",
            "Entry: " + str(entry_price),
            "Take Profit: " + str(take_profit),
            "Stop Loss: " + str(stop_loss),
            "Time: " + current_time_string
        ]
        send_message(bot_token, message)

    except oandapyV20.exceptions.V20Error as e:
        print(f"Error executing sell trade: {str(e)}")


def enter_sell():
    # Fetch historical candle data for the past 30 minutes
    global entry
    params = {
        'count': 12,  # Fetch 25 candles to ensure availability of the last 30 minutes
        'granularity': "M30"
    }
    r = instruments.InstrumentsCandles(instrument=symbol, params=params)

    try:
        candles_30min = api.request(r)['candles']

        # Check if the price is below the old 30 minutes floor
        if is_below_floor(candles_30min):
            # Check if the previous 30 minutes candle has closed
            if 'c' in candles_30min[-2]['mid']:
                # Fetch historical candle data for the past 15 minutes
                params1 = {
                    'count': 12,  # Fetch 16 candles to ensure availability of the last 15 minutes
                    'granularity': "M15"
                }
                r = instruments.InstrumentsCandles(instrument=symbol, params=params1)

                try:
                    candles_15min = api.request(r)['candles']

                    # Check if the last 15 minutes candle has closed
                    if 'c' in candles_15min[-1]['mid']:
                        # Check for a strong bearish candle on the 15-minute timeframe
                        if is_bearish(candles_15min[-1]):
                            entry_price = float(candles_15min[-1]['mid']['c'])  # Close price of the last candle
                            execute_sell_trade(entry_price)
                            entry = 1
                        else:
                            print('No strong bearish candle found on the 15-minute timeframe')
                    else:
                        print('Last 15 minutes candle has not closed yet')
                except oandapyV20.exceptions.V20Error as e:
                    print(f"Error fetching 15-minute candles: {str(e)}")
            else:
                print('Previous 30 minutes candle has not closed yet')
        else:
            print('Price is not below the old 30 minutes floor')
    except oandapyV20.exceptions.V20Error as e:
        print(f"Error fetching 30-minute candles: {str(e)}")


def run_trade_bot():
    # Add your code here
    buy_entry1 = 0
    sell_entry1 = 0

    while running and entry == 0:
        if entry == 0:
            enter_buy()
            buy_entry1 = 1
        else:
            print("NO BUY ENTRY")

        if entry == 0:
            enter_sell()
            sell_entry1 = 1
        else:
            print("NO SELL ENTRY")

        # Get the current time
        current_time = datetime.datetime.now()

        # Format the current time as a string
        current_time_string = current_time.strftime("%H:%M:%S")
        print("Time : ", current_time_string)

        message = [
            "Currency: " + symbol,
            "Roof: " + str(roof),
            "Floor: " + str(floor),
            "Time: " + current_time_string
        ]
        check = 0
        global checkcount

        if checkcount % 6 == 0:
            send_message(bot_token, message)

        checkcount += 1

        time.sleep(interval_minutes * 60)


# Route to update the values and send them to the frontend
@app.route('/update_values', methods=['POST'])
def update_values():
    selected_currency = request.json['currency']

    # Prepare the response data
    response = {
        'success': True,
        'roof': roof,
        'floor': floor
    }

    return jsonify(response)


@app.route('/get_values', methods=['GET'])
def get_values():
    # Replace this with your code to retrieve the updated roof and floor values

    # Prepare the response data
    response = {
        'success': True,
        'roof': roof,
        'floor': floor
    }

    return jsonify(response)

@app.route("/start-tradebot")
def start_tradebot():
    # Add your code here to start the TradeBot
    running = True
    while running:
        run_trade_bot()
        time.sleep(1)  # Sleep for 1 second between each run

# Rest of your code...


def enqueue_start_tradebot():
    job = q.enqueue(start_tradebot)  # Enqueue a new job to start the TradeBot
    return f"TradeBot job enqueued with ID: {job.id}"

@app.route("/stop-tradebot")
def stop_tradebot():
    # Get all the currently running TradeBot jobs
    started_jobs = StartedJobRegistry(queue=q)
    job_ids = started_jobs.get_job_ids()

    # Cancel and remove all the running TradeBot jobs
    for job_id in job_ids:
        job = Job.fetch(job_id, connection=q.connection)
        job.cancel()

    return f"TradeBot jobs stopped: {len(job_ids)}"



@app.route('/entry-details', methods=['GET'])
def get_entry_details():
    return jsonify(entry_details)


@app.route('/')
def home():
    return render_template("index.html")

def run_worker():
    # Start the RQ worker to process jobs in the background
    with Connection(q.connection):
        worker = Worker([q])
        worker.work()
        
        
def send_message(bot_token, message):
    api_url = f"https://api.telegram.org/bot{bot_token}/getUpdates"

    try:
        # Make a request to get the latest updates
        response = requests.get(api_url)

        if response.status_code == 200:
            # Extract the chat ID from the received message
            updates = response.json().get('result', [])
            if updates:
                chat_id = updates[-1]['message']['chat']['id']

                # Send the message using the obtained chat ID
                send_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                payload = {
                    'chat_id': chat_id,
                    "text": "\n".join(message),
                    "parse_mode": "HTML"
                }

                # Make a request to send the message
                response = requests.post(send_url, json=payload)

                if response.status_code == 200:
                    print("Message sent successfully!")
                else:
                    print(f"Failed to send message. Error: {response.status_code}")
            else:
                print("No updates found.")
        else:
            print(f"Failed to get updates. Error: {response.status_code}")

    except requests.exceptions.RequestException as e:
        print(f"Failed to send message. Error: {str(e)}")


bot_token = '6357819400:AAG885Lit52WKOYcNJ8roYbvAdK8gqcEfCA'


if __name__ == '__main__':
    

    # Start the Flask app
    app.run(debug=False,host='0.0.0.0')

    
    
