#!/usr/bin/env python
# pylint: disable=unused-argument
# This program is dedicated to the public domain under the CC0 license.

"""
Simple Bot to reply to Telegram messages.

First, a few handler functions are defined. Then, those functions are passed to
the Application and registered at their respective places.
Then, the bot is started and runs until we press Ctrl-C on the command line.

Usage:
Basic Echobot example, repeats messages.
Press Ctrl-C on the command line or send a signal to the process to stop the
bot.
"""
import math
from operator import itemgetter
import pprint
import os
import logging
import requests

from dotenv import load_dotenv
from telegram import ForceReply, Update
from telegram.ext import Application, CommandHandler, ContextTypes, PicklePersistence

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

load_dotenv()

# Define a few command handlers. These usually take the two arguments update and
# context.
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        rf"Hi {user.mention_html()}! This bot enables you to subscribe to bus stops in taiwan. For info on subscribing trigger the /help command",
        reply_markup=ForceReply(selective=True),
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text("Usage: /subscribe <city> <route> <direction> <station> - /subscribe Taipei 672 1 博仁醫院 - direction is 0 for going route, 1 for return route")

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alert bus is on the way."""
    try:
        chat_id = update.message.chat_id
        #TODO: city & bus_id validation
        Data = {
            'city' : context.args[0],
            'route' : context.args[1],
            'direction' : context.args[2],
            'station' : context.args[3],
        }
        station = await query_tdx(context,
                            city=Data["city"],
                            route=Data["route"],
                            station=Data["station"],
                            direction=Data["direction"]
                            )
        
        if station == 404:
            raise ValueError('Station/Route not found. Make sure the direction you are using is correct')
        
        route_last_station = await query_last_station_tdx(context,
                            city=Data["city"],
                            route=Data["route"],
                            direction=Data["direction"]
                            )
        
        Data["last_station"] = route_last_station

        sub_id = list(Data.values())
        context.job_queue.run_repeating(polling_tdx, 60, chat_id=chat_id, name='-'.join(sub_id + [str(chat_id)]), data=Data)
        
        text = "Subscribed to {} going for {} on station {}".format(Data["route"], Data["last_station"], Data["station"])
        await update.message.reply_text(text)
    except (TypeError,IndexError):
        await update.effective_message.reply_text("Usage: /subscribe <city> <route> <direction> <station>")
    except ValueError as e:
        remove_job_if_exists(str(chat_id), context)
        await update.effective_message.reply_text(f'Bus Error: {e}')

async def polling_tdx(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    
    try:
        station = await query_tdx(context,
                            city=job.data["city"],
                            route=job.data["route"],
                            station=job.data["station"],
                            direction=job.data["direction"]
                            )
        if station['StopStatus'] == 0:
            minutes = math.floor(station["EstimateTime"]/60)
            if minutes < 1:
                await context.bot.send_message(job.chat_id, text=f'Bus {job.data["route"]}->{job.data["last_station"]} is coming to {job.data["station"]} now!')
            elif minutes < 6:
                await context.bot.send_message(job.chat_id, text=f'Bus {job.data["route"]}->{job.data["last_station"]} is coming to {job.data["station"]} in {minutes} minutes!')
            else:
                await context.bot.send_message(job.chat_id, text=f'Bus {job.data["route"]}->{job.data["last_station"]} is coming to {job.data["station"]} in {minutes} minutes!')
        else:
            print("bus still far far away")

    except Exception as e:
        print(f'polling_tdx: {e}')
        return 500
    
async def query_tdx(context: ContextTypes.DEFAULT_TYPE, city, route, station, direction):
    try:
        session = context.bot_data["session"]
        filtere = "Direction eq {} and StopName/Zh_tw eq '{}'".format(direction, station)
        r = session.get(f"https://tdx.transportdata.tw/api/basic/v2/Bus/EstimatedTimeOfArrival/City/{city}/{route}?$top=50&$filter={filtere}&$format=JSON").json()
        return r[0]
    except IndexError as e:
        print(f'query_tdx: {e}')
        return 404
    except Exception as e:
        print(f'query_tdx: {e}')
        return 400
    
async def query_last_station_tdx(context: ContextTypes.DEFAULT_TYPE, city, route, direction):
    try:
        session = context.bot_data["session"]
        r = session.get(f"https://tdx.transportdata.tw/api/basic/v2/Bus/Route/City/{city}/{route}?$top=50&$format=JSON").json()[0]
        pprint.pprint(f'{city},{r["DepartureStopNameZh"]},{r["DestinationStopNameZh"]},{route},{direction}')
        if direction == 0:
            return r["DestinationStopNameZh"]
        else:
            return r["DepartureStopNameZh"]
    except Exception as e:
        print(f'query_last_station_tdx: {e}')
        return 101

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Removed all subscription if the user changed their mind."""
    job_names = [job.name for job in context.job_queue.jobs()]
    if len(job_names) == 0:
        await update.message.reply_text("You have no active subscription.")
    else:
        for job_name in job_names:
            job_removed = remove_job_if_exists(job_name, context)

        await update.message.reply_text("Removed all Subscription!")

async def subscribe_closest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check for next closest bus."""
    chat_id = update.message.chat_id
    city = context.args[0]
    route = context.args[1]
    direction = context.args[2]
    station = context.args[3]
    try:
        session = context.bot_data["session"]
        filtere = "Direction eq {}".format(direction)
        r = session.get(f"https://tdx.transportdata.tw/api/basic/v2/Bus/DisplayStopOfRoute/City/{city}/{route}?$top=50&$filter={filtere}&$format=JSON").json()[0]
        stop = next(stop for stop in r["Stops"] if stop["StopName"]["Zh_tw"] == station)

        Data = {
            'city': city,
            'route': route,
            'direction': direction,
            'stop': stop
        }

        last_station = await query_last_station_tdx(context,
                            city=Data["city"],
                            route=Data["route"],
                            direction=Data["direction"]
                            )
        
        Data['last_station'] = last_station
        
        sub_id = [city,route,direction,station]
        pprint.pprint(stop)
        context.job_queue.run_repeating(polling_closest, 60, chat_id=chat_id, name='-'.join(sub_id + [str(chat_id)]), data=Data)

        text = "Subscribed (C) to {} going for {} on station {}".format(route, last_station, station)
        # pprint.pprint(a)
    except Exception as e:
        print(f'subscribe_closest: {e}')
        return 400
    else:
        await update.message.reply_text(text)

async def polling_closest(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    
    try:
        city, route, direction, stop, last_station = itemgetter('city', 'route', 'direction', 'stop', 'last_station')(job.data)
        session = context.bot_data["session"]
        filtere = "Direction eq {}".format(direction)
        buses = session.get(f"https://tdx.transportdata.tw/api/basic/v2/Bus/RealTimeNearStop/City/{city}/{route}?$top=50&$filter={filtere}&$format=JSON").json()
        upcoming_bus_stop_sequences = [bus["StopSequence"] for bus in buses if bus["StopSequence"] < stop["StopSequence"]]
        pprint.pprint(upcoming_bus_stop_sequences)
        if len(upcoming_bus_stop_sequences) > 0:
            next_upcoming_bus = max(upcoming_bus_stop_sequences)
            station_diff = stop["StopSequence"] - next_upcoming_bus
            if station_diff < 5:
                await context.bot.send_message(job.chat_id, text=f'Bus {route}->{last_station} is coming to {stop["StopName"]["Zh_tw"]} by {station_diff} station!')
            # elif minutes < 6:
            #     await context.bot.send_message(job.chat_id, text=f'Bus {job.data["route"]}->{job.data["last_station"]} is coming to {job.data["station"]} in {minutes} minutes!')
            # else:
            #     await context.bot.send_message(job.chat_id, text=f'Bus {job.data["route"]}->{job.data["last_station"]} is coming to {job.data["station"]} in {minutes} minutes!')
        else:
            print("bus still far far away")


    except Exception as e:
        print(f'polling_closest: {e}')
        return 500

def remove_job_if_exists(name: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Remove job with given name. Returns whether job was removed."""
    current_jobs = context.job_queue.get_jobs_by_name(name)
    if not current_jobs:
        return False
    for job in current_jobs:
        job.schedule_removal()
    return True


def authenticate_tdx(client_id, client_secret):
    data = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret
    }
    r = requests.post('https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token', data=data).json()
    return r['access_token']

def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    
    try:
        client_id = os.environ.get('TDX_CLIENT_ID')
        client_secret = os.environ.get('TDX_CLIENT_SECRET')
        telegram_secret = os.environ.get('TELEGRAM_SECRET')

        # TODO: add persistence

        # Authenticate Telegram
        application = Application.builder().token(telegram_secret).build()

    except Exception as e:
        print(f'Authentication failure: {e}')
        return 101
    
    else:
        # save bearer token
        s = requests.Session()
        application.bot_data["session"] = s

        # Authenticate TDX
        def refresh_tdx_token(r, *args, **kwargs):
            if r.status_code == 401:
                tdx_token = authenticate_tdx(client_id=client_id, client_secret=client_secret)
                Headers = { 'authorization': 'Bearer {}'.format(tdx_token) }
                s.headers.update(Headers)
                r.request.headers["Authorization"] = s.headers["Authorization"]
                return s.send(r.request)

        s.hooks['response'].append(refresh_tdx_token)

        # on different commands - answer in Telegram
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("subscribe", subscribe))
        application.add_handler(CommandHandler("unsubscribe", unsubscribe))
        application.add_handler(CommandHandler("sub", subscribe))
        application.add_handler(CommandHandler("unsub", unsubscribe))
        application.add_handler(CommandHandler("subc", subscribe_closest))
        # TODO:application.add_handler(CommandHandler("list", list_subscription))
        # TODO:application.add_handler(CommandHandler("list", list_city))
        # TODO:application.add_handler(CommandHandler("list", list_direction))

        # Run the bot until the user presses Ctrl-C
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

# TODO: Start documentation