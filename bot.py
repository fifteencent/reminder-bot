import discord
from discord.ext.tasks import loop
import datetime
from dateutil.parser import parse
import os
import schedule
import csv
import random as rand
import re

# days is array of booleans representing whether the task is scheduled for that day (Mon -> Sun)
class Task:
    def __init__(self, user, name, isClass, message, time, days, recurring):
        self.user = user  # discord user object
        self.name = str(name)
        self.isClass = isClass
        self.message = str(message)
        self.days = days
        self.time = parse(str(time)).time()  # datetime time object
        self.recurring = recurring


class User:
    def __init__(self, username, discordID, sheetID):
        self.username = username
        self.discordID = str(discordID)
        self.sheetID = sheetID
        self.lastTask = None

    def setSheetID(self, sheetID):
        self.sheetID = sheetID

    def setLastTask(self, task):
        self.lastTask = task

# needed for schedule workaround
def nothing():
    pass


tasks = dict()  # maps time object to set of # s to be (possibly) run at that time
schedules = dict()  # maps time object to corresponding schedule object
users = dict()  # maps discordID's (as strings) to user objects
remindMessages = []
pullSheets = schedule.every().day.at('04:00').do(nothing)  # update info every day at 4:00 AM


def readInUsers(client):
    global users
    users.clear()
    with open("users.txt", "r") as f:
        lines = f.readlines()
        for line in lines:
            entry = line.strip("\n").split(":")
            if entry[0] == '':
                return
            discordID = entry[0]
            try:
                sheetID = entry[1]
            except:
                sheetID = None
            user = User(username=str(client.get_user(int(discordID)))[:-5].replace(" ", "_"), discordID=discordID, sheetID=sheetID)
            users[str(discordID)] = user


def getBool(inputStr):
    if inputStr.lower() == "true":
        return True
    if inputStr.lower() == "false":
        return False
    return None


# goes through user's spreadsheet and reads in tasks
# either adds or removes tasks depending on boolean 'remove' parameter
def modifyUserTasks(user, remove):
    if user.sheetID is None:
        return
    directory = os.path.dirname(__file__)
    relPath = 'sheets/' + user.username + '.csv'
    absPath = os.path.join(directory, relPath)

    try:
        csvFile = open(absPath, newline='')
    except:
        os.system("touch " + absPath)
        if remove is True:
            return  # no need to clear csv file if it didn't exist
    csvFile = open(absPath, newline='')

    csvReader = csv.reader(csvFile, delimiter=',', quotechar='|')
    next(csvReader)  # skip heading row
    for row in csvReader:
        # construct task
        days = list(map(lambda x: getBool(x), row[4:11]))
        try:
            parse(str(row[3])).time()
        except:
            continue
        task = Task(user=user, name=row[0], isClass=getBool(row[1]), message=row[2], time=row[3], days=days, recurring=True)

        # add or remove task
        try:
            if remove:
                # was causing double reminder issue - updated sheetID caused mismatch between user objects of tasks
                # tasks[task.time].remove(task)
                # removes any tasks scheduled by user at this time (will be replacing later)
                tasks[task.time] = {i for i in tasks[task.time] if i.user.discordID != user.discordID}
            else:
                tasks[task.time].add(task)
        except:
            if not remove:
                tasks[task.time] = {task}
                schedules[task.time] = schedule.every().day.at(task.time.strftime("%H:%M")).do(nothing)


# removes user's tasks, updates spreadsheet, adds new tasks (and old ones back in)
# should be called on program start and after any sheetUpdate()'s + once per day?
def updateUserTasks(user):
    modifyUserTasks(user, True)
    updateSheet(user)
    modifyUserTasks(user, False)


def updateAllUserTasks():
    for user in users.values():
        updateUserTasks(user)


# removes user if add is False and adds otherwise
# option to update sheetID is parameter is not None
def modifyUserStatus(id, add, sheetID):
    global users
    # remove from list in memory
    if not add:
        try:
            del users[str(id)]
            print('deleting user')
        except:
            print('User not in file.')

    # add/remove user to/from text file
    idStr = str(id)
    prevSheetID = None
    with open("users.txt", "r") as f:
        lines = f.readlines()
    with open("users.txt", "w") as f:
        for line in lines:
            IDs = line.split(":")  # format is discordID:sheetID
            if IDs[0].strip("\n") != idStr:
                f.write(line)
            else:
                try:
                    prevSheetID = IDs[1]
                except:
                    pass
        if add:
            if sheetID is not None:
                f.write(idStr + ":" + sheetID + "\n")
            elif prevSheetID is not None:
                f.write(idStr + ":" + prevSheetID + "\n")
            else:
                f.write(idStr + "\n")


def updateMessages():
    # update remind messages sheet
    commandTemplate = "wget --no-check-certificate -O {0}.csv " \
                 "'https://docs.google.com/spreadsheets/d/1sbAwZLAg3sBd2p5RHuSiRjPt4eV5JG2vkkiTsup-7Gg/export?gid=0&format=csv'"

    os.system(commandTemplate.format('remind-messages'))


def readInMessages():
    global remindMessages
    updateMessages()

    remindMessages = []  # clear list
    csvFile = open('remind-messages' + '.csv', newline='')
    csvReader = csv.reader(csvFile, delimiter='\n', quotechar='|')
    for row in csvReader:
        message = str(row[0]).replace("\"", "").replace('{class}', '{0}')
        remindMessages.append(message)  # replace with 0 for string formatting

    csvFile.close()


def getRandomMessage():
    index = rand.randint(0, len(remindMessages)-1)
    return str(remindMessages[index])


async def sendReminders(time, client):
    weekday = datetime.datetime.now().weekday()  # zero-indexed starting on Monday
    for task in tasks[time]:
        if task.days[weekday]:  # if task should run on this day
            messageTemplate = "{0} time!"
            if task.isClass:
                messageTemplate = getRandomMessage()
            messageText = messageTemplate.format(task.name) + "  " + task.message
            print('sending reminder to ' + task.user.username)
            users[task.discordID].setLastTask(task)
            if not task.recurring:
                tasks[time].remove(task)
            await client.get_user(int(task.user.discordID)).send(messageText)


# pulls user sheet data
def updateSheet(user):
    if user.sheetID is not None:
        commandTemplate = "(wget --no-check-certificate -O sheets/{0}.csv \'https://docs.google.com/spreadsheets/d/{1}/export?gid=0&format=csv\')"

        # download sheet data to local csv
        command = commandTemplate.format(str(user.username), str(user.sheetID))
        os.system(command)


def setSheetID(client, user, url):
    # extract file ID from URL
    expression = re.compile(r"[-\w]{25,}")
    sheetID = expression.search(url)
    if sheetID is not None:
        modifyUserStatus(user.id, True, sheetID.group())
        readInUsers(client)


async def welcome(user):
    await user.send("Please send a message with 'sheet =' followed by the public link to your google sheet which is a copy of this template: \n"
                    "https://docs.google.com/spreadsheets/d/1kuIeBz1Jwq9lxVKueG4mvZwXaWb493Tpx1WS7PZ2slA/edit?usp=sharing\n"
                    "(see instructions tab on sheet for more instructions about formatting, etc.)")


@loop(seconds=5)
async def checkRemind():
    for time in schedules.keys():
        if schedules[time].should_run:
            await sendReminders(time, client)
            schedules[time].last_run = datetime.datetime.now()
            schedules[time]._schedule_next_run()
    if pullSheets.should_run:
        updateAllUserTasks()
        pullSheets.last_run = datetime.datetime.now()
        pullSheets._schedule_next_run()


class MyClient(discord.Client):
    async def on_ready(self):
        print('Logged on as {0}!'.format(self.user))
        # todo: IMPORTANT INIT THINGS
        readInMessages()
        readInUsers(client)
        for user in users.values():
            updateSheet(user)
            modifyUserTasks(user, False)

    async def on_message(self, message):
        if message.author.id == 702617503888703488:  # ignore messages from self
            return

        messageText = str(message.content).lower().replace(' ', '').replace('\n', '')

        if messageText == "start-reminders":
            modifyUserStatus(message.author.id, True, None)
            await welcome(message.author)
            print("started for: " + message.author.name)

        if messageText == "stop-reminders":
            modifyUserStatus(message.author.id, False, None)
            await message.channel.send("You have stopped reminders. ")
            print("stopped for: " + message.author.name)

        if "sheet=" in messageText:
            setSheetID(client, message.author, message.content.replace("sheet", "").strip(" "))
            updateUserTasks(users[str(message.author.id)])
            await message.channel.send("You updated your spreadsheet ID. ")

        if "add-user" in messageText:
            try:
                ID = int(messageText.replace('add-user', ''))
                modifyUserStatus(ID, True, None)
                readInUsers(client)
                await welcome(client.get_user(ID))
                # print('Welcoming ' + str(client.get_user(ID).username))
                print('Welcoming new user. ')
            except:
                print('tried to add-user with non-integer ID')

        if "update-messages" in messageText:
            updateMessages()
            await message.channel.send("Messages have been updated.")

        if messageText == "update":
            updateUserTasks(users[str(message.author.id)])
            await message.channel.send("Your schedule information has been updated!")

        if "delay" in messageText:
            newMessage = ''
            try:
                time = [int(word) for word in messageText.split() if word.isdigit()]
                time = time[0]
                unit = ''
                if "week" in message.content:  
                    time = datetime.datetime.now() + datetime.timedelta(days=time*7)
                    unit = 'week' if time == 1 else 'weeks'
                    newMessage = 'Delaying for ' + str(time) + unit + '!'
                elif "day" in message.content:  
                    time = datetime.datetime.now() + datetime.timedelta(days=time)
                    unit = 'day' if time == 1 else 'days'
                    newMessage = 'Delaying for ' + str(time) + unit + '!'
                elif "hour" in message.content:  
                    time = datetime.datetime.now() + datetime.timedelta(hours=time)
                    unit = 'hour' if time == 1 else 'hours'
                    newMessage = 'Delaying for ' + str(time) + unit + '!'
                elif "minute" in message.content:  
                    time = datetime.datetime.now() + datetime.timedelta(minutes=time)
                    unit = 'minute' if time == 1 else 'minutes'
                    newMessage = 'Delaying for ' + str(time) + unit + '!'
                elif "second" in message.content:  
                    time = datetime.datetime.now() + datetime.timedelta(seconds=time)
                    unit = 'second' if time == 1 else 'seconds'
                    newMessage = 'Delaying for ' + str(time) + unit + '!'
                
                task = users[message.author.id].lastTask
                newTask = Task(user=task.user, name=task.name, isClass=task.isClass, message=task.message, time=time, days=task.days)
                tasks[time].add(newTask)
            except:
                newMessage = 'Please use the format "delay for (number) of (unit)" (WITH SPACES :eyes:). Some units include days, minutes, weeks.'
            await message.channel.send(newMessage)

client = MyClient()
checkRemind.start()

# read in token from local file
with open("token.txt", "r") as f:
    token = f.readlines()[0]

client.run(token)