import base64
import os
from utils import Utils
from datetime import datetime
import hypercorn.asyncio
from quart import Quart, render_template_string, request, render_template, redirect
from telethon import TelegramClient, utils, events
from telethon.tl import functions
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.functions.channels import CreateChannelRequest, CheckUsernameRequest, UpdateUsernameRequest
from telethon.tl.types import InputChannel, InputPeerChannel, UpdateMessagePoll, UpdateMessagePollVote
from telethon.tl.types import InputPeerEmpty
from pymongo import MongoClient
from dbUtils import DBUtils
from gsheets import GSheets
from configparser import ConfigParser

config = ConfigParser()
config.read('conf.ini')

API_ID = config['CONF']['API_ID']
API_HASH = config['CONF']['API_HASH']
PHONE_NUMBER = config['CONF']['PHONE_NUMBER_IN_INTERNATIONAL_FORMAT']
DB_URL = config['CONF']['DB_URL']

mongoClient = MongoClient(DB_URL)
db = mongoClient.telegramDB
dbUtils = DBUtils(db)
sheets = GSheets(db)


# Telethon client
client = TelegramClient(PHONE_NUMBER, API_ID, API_HASH)
client.parse_mode = 'html'  # <- Render things nicely
phone = None

# Quart app
app = Quart(__name__)
app.secret_key = 'CHANGE THIS TO SOMETHING SECRET'
logged_in = True


# Samples for testing
list_of_channels = None

# ##################################################################< Telegram Section >##################################################################


# Telegram events

@client.on(events.NewMessage)
async def my_event_handler(event):
    return
    if 'hello' in event.raw_text:
        await event.reply('hi!')


@client.on(events.Raw(types=[UpdateMessagePoll]))
async def poll(event):
    pollId = event.poll_id
    # print(event.stringify())
    if not await dbUtils.pollExists(pollId):
        return
    chosenAnswer = await dbUtils.getSelected(pollId, event.results.results)
    pollGroup = await dbUtils.getPollGroup(pollId)
    value = await dbUtils.ifCorrect(pollId, chosenAnswer.option)
    newVoterId = event.results.recent_voters[0]
    newVoterObject = await client.get_entity(int(newVoterId))
    current_date = datetime.now()
    shTitle = f'{pollGroup["groupName"]}_{current_date.year}_{current_date.month}'
    wsTitle = f'{current_date.month}_{current_date.day}'
    sheetUrl = await dbUtils.getSheetUrl(shTitle, groupId=pollGroup['groupId'])
    exists, userRow = await sheets.userExists(sheetUrl, wsTitle, newVoterId)
    if not exists:
        fName = newVoterObject.first_name if newVoterObject.first_name else ''
        lName = newVoterObject.last_name if newVoterObject.last_name else ''
        name = f'{fName} {lName}'
        await sheets.addUser(sheetUrl, wsTitle, [newVoterId, name])
    exists, userRow = await sheets.userExists(sheetUrl, wsTitle, newVoterId)
    await sheets.append_col(sheetUrl, wsTitle, userRow, value)

    # ##################################################################< Quart Routes >##################################################################

    # Connect the client before we start serving with Quart


@app.before_serving
async def startup():
    global my_utils
    await client.connect()
    if not await client.is_user_authorized():
        await client.send_code_request(PHONE_NUMBER)
        await client.sign_in(code=input("Enter code: "))
    my_utils = Utils(client, dbUtils, sheets)


# After we're done serving (near shutdown), clean up the client
@app.after_serving
async def cleanup():
    await client.disconnect()


@app.route('/', methods=['GET'])
async def login():
    if not logged_in:
        return await render_template('login.html')
    return redirect('/dashboard')


@app.route('/logout', methods=['GET'])
async def logout():
    global logged_in
    logged_in = False
    return redirect(f'/')


@app.route('/dashboard', methods=['GET'])
async def dashboard():
    global logged_in
    if logged_in:
        return await render_template('dashboard.html')
    return redirect(f'/')


@app.route('/login', methods=['POST'])
async def verify_login():
    global logged_in
    form_data = await request.form
    username = form_data.get("username", None)
    password = form_data.get("password", None)
    if username == 'admin':
        if password == 'pass':
            logged_in = True
            return redirect('/dashboard')
    return redirect(f'/')


@app.route('/create_new_channel', methods=['GET', 'POST'])
async def create_new_channel():
    global logged_in
    if not logged_in:
        return redirect(f'/')
    if request.method == 'GET':
        return await render_template('create_channel.html')
    if request.method == 'POST':
        form_data = await request.form
        channel_name = form_data.get("channelName", None)
        channel_desc = form_data.get("channelDesc", None)
        channel_type = form_data.get("type", None)
        # TODO: Improve channel creation
        if channel_type != None:
            createdPrivateChannel = await client(CreateChannelRequest(channel_name, channel_desc, megagroup=False))
            created_channenl_name = createdPrivateChannel.chats[0].title
        return await render_template_string(f'Channel Created! {created_channenl_name}')


@app.route('/all_channels', methods=['GET'])
async def all_channel():
    global logged_in
    global list_of_channels
    if not logged_in:
        return redirect(f'/')
    if list_of_channels == None:
        list_of_channels = await my_utils.list_of_channels()
    return await render_template('list_of_channels.html', channels=list_of_channels)


@app.route('/remove_member', methods=['GET', 'POST'])
async def remove_member():
    global logged_in
    global list_of_channels
    if not logged_in:
        return redirect(f'/')
    if request.method == 'GET':
        channel_id = request.args.get('channel_id')
        return await render_template('member.html', remove=True, channel_id=channel_id)
    if request.method == 'POST':
        form = await request.form
        channel_id = form.get('channel_id')
        type_of_en = form.get('type')
        en = form.get('input_entity')
        if type_of_en == 'id':
            await my_utils.remove_member_by_id(int(channel_id), int(en))
        if type_of_en == 'username':
            await my_utils.remove_member_by_username(int(channel_id), en)
        if type_of_en == 'phone':
            await my_utils.remove_member_by_phone(int(channel_id), en)

        return await render_template_string('Removing member ...')


@app.route('/add_member', methods=['GET', 'POST'])
async def add_member():
    global logged_in
    global list_of_channels
    if not logged_in:
        return redirect(f'/')
    if request.method == 'GET':
        channel_id = request.args.get('channel_id')
        return await render_template('member.html', add_new=True, channel_id=channel_id)
    if request.method == 'POST':
        form = await request.form
        channel_id = form.get('channel_id')
        type_of_en = form.get('type')
        en = form.get('input_entity')
        if type_of_en == 'id':
            await my_utils.remove_member_by_id(int(channel_id), int(en))
        if type_of_en == 'username':
            await my_utils.remove_member_by_username(int(channel_id), en)
        if type_of_en == 'phone':
            await my_utils.remove_member_by_phone(int(channel_id), en)

        return await render_template_string('Adding member ...')


@app.route('/get_invite', methods=['GET', 'POST'])
async def get_invite():
    global logged_in
    global list_of_channels
    if not logged_in:
        return redirect(f'/')
    if request.method == 'GET':
        channel_id = request.args.get('channel_id')
        link = await my_utils.get_invite_link(int(channel_id))
        return await render_template('utils.html', display_link=True, link=link)


@app.route('/schedule_quiz', methods=['GET', 'POST'])
async def create_quiz():
    global logged_in
    if not logged_in:
        return redirect(f'/')
    if request.method == 'GET':
        channel_id = int(request.args.get('channel_id'))
        list_of_quiz = await my_utils.get_list_of_polls(channel_id)
        if len(list_of_quiz) != 0:
            return await render_template('schedule_quiz.html', list_quiz=True, list_of_quiz=list_of_quiz, channel_id=channel_id)
        return await render_template('schedule_quiz.html', list_quiz=False, channel_id=channel_id)
    if request.method == 'POST':
        hours = list(range(0, 24))
        minutes = list(range(0, 60))
        days = list(range(1, 32))
        months = list(range(1, 13))
        dates = {
            'hours': hours,
            'minutes': minutes,
            'days': days,
            'months': months,
            'weekdays': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        }
        action = request.args.get('action')
        if action == 'add_new':
            channel_id = request.args.get('channel_id')
            return await render_template('schedule_quiz.html', add_new=True, channel_id=channel_id, dates=dates)
        if action == 'save_quiz':
            form = await request.form
            question = form.get('question')
            channel_id = form.get('channel_id')
            correctAnswer = form.get('correctAnswer')
            answers = []
            answers.append(correctAnswer)
            answersCount = 0
            while True:
                answersCount += 1
                answer = form.get(f'option{answersCount}')
                if not answer:
                    break
                answers.append(answer)
            cat = form.get('scheduleCat')
            if cat == 'once':
                dom = int(form.get('onceDom'))
                month = int(form.get('onceMonth'))
                hours = int(form.get('onceHours'))
                minutes = int(form.get('onceMinutes'))
                status = await my_utils.schedule_poll(int(channel_id), question=question, answers=answers, poll_typ='text', month=month, day=dom, hour=hours, minute=minutes)
                if status['code'] != 0:
                    return await render_template('success.html', link='/all_channels', link_text='Back to channels', title='Schedule failed!', description=f'{status["message"]}')
                return await render_template('success.html', link='/all_channels', link_text='Back to channels', title='Scheduled successfully!', description=f'{status["message"]}')
            if cat == 'from':
                fromHours = int(form.get('fromHours'))
                fromMinutes = int(form.get('fromMinutes'))
                fromDay = int(form.get('fromDay'))
                fromMonth = int(form.get('fromMonth'))
                untilDay = int(form.get('fromUntilDay'))
                untilMonth = int(form.get('fromUntilMonth'))
                keepGoing = True
                while keepGoing:
                    status = await my_utils.schedule_poll(int(channel_id), question=question, answers=answers, poll_typ='text', month=fromMonth, day=fromDay, hour=fromHours, minute=fromMinutes)
                    if fromMonth == untilMonth:
                        if fromDay == untilDay:
                            break
                    if fromDay < untilDay:
                        fromDay += 1
                        continue
                    if fromMonth < untilMonth:
                        fromMonth += 1
                        fromDay = 1
                        continue
                if status['code'] != 0:
                    return await render_template('success.html', link='/all_channels', link_text='Back to channels', title='Schedule failed!', description=f'{status["message"]}')
                return await render_template('success.html', link='/all_channels', link_text='Back to channels', title='Scheduled successfully!', description=f'{status["message"]}')
            if cat == 'weekdays':
                day_of_week = form.get('weekdaysDay')
                hours = form.get('weekdaysHours')
                minutes = form.get('weekdaysMinutes')
                return str(day_of_week) + str(hours) + str(minutes)
            if cat == '':
                return await render_template_string('Please select a Time category ...')
            return await render_template('schedule_quiz.html', saved=True)

        return await render_template_string('No match!')


@app.route('/test', methods=['GET', 'POST'])
async def test():
    global logged_in
    if not logged_in:
        return redirect(f'/')
    if request.method == 'GET':
        return await render_template('test.html', get_data=True, lis=[0, 2, 3, 56])
    if request.method == 'POST':
        return await render_template('test.html')


@app.route('/schedule_message', methods=['GET', 'POST'])
async def manage_content():
    global logged_in
    if not logged_in:
        return redirect(f'/')
    if request.method == 'GET':
        form = await request.form
        channel_id = request.args.get('channel_id')
        channel_title = request.args.get('channel_title')
        messages = await my_utils.get_scheduled_messages(await client.get_input_entity(int(channel_id)))
        if messages is not None:
            return await render_template('schedule_message.html', list_all=True, channel_id=channel_id, channel_title=channel_title, messages=messages)
        return await render_template('schedule_message.html', channel_id=channel_id, list_all=True, channel_title=channel_title)
    if request.method == 'POST':
        hours = list(range(0, 24))
        minutes = list(range(0, 60))
        days = list(range(1, 31))
        months = list(range(1, 13))
        dates = {
            'hours': hours,
            'minutes': minutes,
            'days': days,
            'months': months,
            'weekdays': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        }
        action = request.args.get('action')
        if action == 'get_type':
            form = await request.form
            channel_id = form.get('channel_id')
            return await render_template('schedule_message.html', get_type=True, channel_id=channel_id)
        if action == 'get_message':
            form = await request.form
            channel_id = form.get('channel_id')
            type_of_message = form.get('type')
            if type_of_message == 'Text/Link':
                return await render_template('schedule_message.html', channel_id=channel_id, get_text=True, dates=dates)
            if type_of_message == 'Image/Video':
                return await render_template('schedule_message.html', channel_id=channel_id, get_file=True, dates=dates)
        if action == 'save_text_content':
            form = await request.form
            channel_id = int(form.get('channel_id'))
            cat = form.get('scheduleCat')
            message = form.get('message_text')
            if cat == 'once':
                dom = form.get('onceDom')
                month = form.get('onceMonth')
                hours = form.get('onceHours')
                minutes = form.get('onceMinutes')
                await my_utils.schedule_message_once(channel_id, 'text', message_text=message, month=int(month), day=int(dom), hour=int(hours), minute=int(minutes))
                return await render_template('success.html', link='/all_channels', link_text='Back To Channels', title='Message Scheduled!')
            if cat == 'from':
                fromHours = int(form.get('fromHours'))
                fromMinutes = int(form.get('fromMinutes'))
                fromDay = int(form.get('fromDay'))
                fromMonth = int(form.get('fromMonth'))
                untilDay = int(form.get('fromUntilDay'))
                untilMonth = int(form.get('fromUntilMonth'))
                keepGoing = True
                while keepGoing:
                    await my_utils.schedule_message_once(channel_id, 'text', message_text=message, month=fromMonth, day=fromDay, hour=fromHours, minute=fromMinutes)
                    if fromMonth == untilMonth:
                        if fromDay == untilDay:
                            break
                    if fromDay < untilDay:
                        fromDay += 1
                        continue
                    if fromMonth < untilMonth:
                        fromMonth += 1
                        fromDay = 1
                        continue

                return await render_template('success.html', link='/all_channels', link_text='Back To Channels', title='Message Scheduled!')

            if cat == 'weekdays':
                day_of_week = form.get('weekdaysDay')
                hours = form.get('weekdaysHours')
                minutes = form.get('weekdaysMinutes')
                return str(day_of_week) + str(hours) + str(minutes)
            if cat == '':
                return await render_template_string('Please select a Time category')

        if action == 'save_file':
            form = await request.form
            cat = form.get('scheduleCat')
            channel_id = form.get('channel_id')
            files = await request.files
            uploaded_file = files.get('file')
            if not os.path.exists('./uploads'):
                os.mkdir('./uploads')
            file_caption = form.get('file_caption')
            if cat == 'once':
                dom = form.get('onceDom')
                month = form.get('onceMonth')
                hours = form.get('onceHours')
                minutes = form.get('onceMinutes')
                filename = f'{datetime.timestamp(datetime.now())}.png'
                file_location = f'./uploads/{filename}'
                uploaded_file.save(file_location)
                await my_utils.schedule_message_once(channel_id, 'file', file_location=file_location, file_caption=file_caption, month=int(month), day=int(dom), hour=int(hours), minute=int(minutes))
                return await render_template('success.html', link='/all_channels', link_text='Back To Channels', title='Message Scheduled!')
            if cat == 'from':
                fromHours = int(form.get('fromHours'))
                fromMinutes = int(form.get('fromMinutes'))
                fromDay = int(form.get('fromDay'))
                fromMonth = int(form.get('fromMonth'))
                untilDay = int(form.get('fromUntilDay'))
                untilMonth = int(form.get('fromUntilMonth'))
                filename = f'{datetime.timestamp(datetime.now())}.png'
                file_location = f'./uploads/{filename}'
                uploaded_file.save(file_location)
                keepGoing = True
                while keepGoing:
                    await my_utils.schedule_message_once(channel_id, 'file', file_location=file_location, file_caption=file_caption, month=fromMonth, day=fromDay, hour=fromHours, minute=fromMinutes)
                    if fromMonth == untilMonth:
                        if fromDay == untilDay:
                            break
                    if fromDay < untilDay:
                        fromDay += 1
                        continue
                    if fromMonth < untilMonth:
                        fromMonth += 1
                        fromDay = 1
                        continue
                return await render_template('success.html', link='/all_channels', link_text='Back To Channels', title='Message Scheduled!')
            if cat == 'weekdays':
                day_of_week = form.get('weekdaysDay')
                hours = form.get('weekdaysHours')
                minutes = form.get('weekdaysMinutes')
                return str(day_of_week) + str(hours) + str(minutes)
            if cat == '':
                return await render_template_string('Please select a Time category')

        return await render_template_string('No match ...')


@app.route('/delete_message', methods=['POST'])
async def delete_message():
    global logged_in
    if not logged_in:
        return redirect(f'/')
    if request.method == 'POST':
        action = request.args.get('action')
        form = await request.form
        message_id = int(form.get('message_id'))
        channel_id = int(form.get('channel_id'))
        print(action)
        if action == 'delete':
            await my_utils.delete_message(channel_id, message_id)
            return 'message deleted'


@app.route('/quiz_reports', methods=['GET'])
async def quiz_reports():
    global logged_in
    if not logged_in:
        return redirect('/')
    if request.method == 'GET':
        channelId = request.args.get('channel_id')
        sheets = await dbUtils.getAllSheets(int(channelId))
        if sheets:
            return await render_template('reports.html', sheets=sheets)


async def main():
    await hypercorn.asyncio.serve(app, hypercorn.Config())


if __name__ == '__main__':
    client.loop.run_until_complete(main())