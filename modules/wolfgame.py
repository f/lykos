# -*- coding: utf-8 -*-

# Copyright (c) 2011, Jimmy Cao All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# Redistributions of source code must retain the above copyright notice, this
# list of conditions and the following disclaimer.  Redistributions in binary
# form must reproduce the above copyright notice, this list of conditions and
# the following disclaimer in the documentation and/or other materials provided
# with the distribution.  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS
# AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING,
# BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER
# OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
# OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
# OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from oyoyo.parse import parse_nick
import settings.wolfgame as var
import botconfig
from tools.wolfgamelogger import WolfgameLogger
from tools import decorators
from datetime import datetime, timedelta
from collections import defaultdict
import threading
import copy
import time
import re
import sys
import os
import imp
import math
import fnmatch
import random
import subprocess
from imp import reload

BOLD = "\u0002"

COMMANDS = {}
PM_COMMANDS = {}
HOOKS = {}

cmd = decorators.generate(COMMANDS)
pmcmd = decorators.generate(PM_COMMANDS)
hook = decorators.generate(HOOKS, raw_nick=True, permissions=False)

# Game Logic Begins:

var.LAST_PING = None  # time of last ping
var.LAST_STATS = None
var.LAST_VOTES = None
var.LAST_ADMINS = None
var.LAST_GSTATS = None
var.LAST_PSTATS = None
var.LAST_TIME = None

var.USERS = {}

var.PINGING = False
var.ADMIN_PINGING = False
var.ROLES = {"kisi" : []}
var.SPECIAL_ROLES = {}
var.ORIGINAL_ROLES = {}
var.PLAYERS = {}
var.DCED_PLAYERS = {}
var.ADMIN_TO_PING = None
var.AFTER_FLASTGAME = None
var.PHASE = "yok"  # "join", "gunduz", or "gece"
var.TIMERS = {}
var.DEAD = []

var.ORIGINAL_SETTINGS = {}

var.LAST_SAID_TIME = {}

var.GAME_START_TIME = datetime.now()  # for idle checker only
var.CAN_START_TIME = 0
var.GRAVEYARD_LOCK = threading.RLock()
var.GAME_ID = 0
var.STARTED_DAY_PLAYERS = 0

var.DISCONNECTED = {}  # players who got disconnected

var.STASISED = defaultdict(int)

var.LOGGER = WolfgameLogger(var.LOG_FILENAME, var.BARE_LOG_FILENAME)

var.JOINED_THIS_GAME = [] # keeps track of who already joined this game at least once (cloaks)

if botconfig.DEBUG_MODE:
    var.NIGHT_TIME_LIMIT = 0  # 90
    var.NIGHT_TIME_WARN = 0
    var.DAY_TIME_LIMIT_WARN = 0
    var.DAY_TIME_LIMIT_CHANGE = 0
    var.SHORT_DAY_LIMIT_WARN = 0
    var.SHORT_DAY_LIMIT_CHANGE = 0
    var.KILL_IDLE_TIME = 0 #300
    var.WARN_IDLE_TIME = 0 #180
    var.JOIN_TIME_LIMIT = 0

        
def connect_callback(cli):
    to_be_devoiced = []
    cmodes = []
    
    @hook("quietlist", hookid=294)
    def on_quietlist(cli, server, botnick, channel, q, quieted, by, something):
        if re.match(".+\!\*@\*", quieted):  # only unquiet people quieted by bot
            cmodes.append(("-q", quieted))

    @hook("whospcrpl", hookid=294)
    def on_whoreply(cli, server, nick, ident, cloak, user, status, acc):
        if user in var.USERS: return  # Don't add someone who is already there
        if user == botconfig.NICK:
            cli.nickname = user
            cli.ident = ident
            cli.hostmask = cloak
        if acc == "0":
            acc = "*"
        if "+" in status:
            to_be_devoiced.append(user)
        var.USERS[user] = dict(cloak=cloak,account=acc)
        
    @hook("endofwho", hookid=294)
    def afterwho(*args):
        for nick in to_be_devoiced:
            cmodes.append(("-v", nick))
        # devoice all on connect
        
        @hook("mode", hookid=294)
        def on_give_me_ops(cli, blah, blahh, modeaction, target="", *other):
            if modeaction == "+o" and target == botconfig.NICK and var.PHASE == "yok":
                
                @hook("quietlistend", 294)
                def on_quietlist_end(cli, svr, nick, chan, *etc):
                    if chan == botconfig.CHANNEL:
                        decorators.unhook(HOOKS, 294)
                        mass_mode(cli, cmodes)
                
                cli.mode(botconfig.CHANNEL, "q")  # unquiet all

                cli.mode(botconfig.CHANNEL, "-m")  # remove -m mode from channel
            elif modeaction == "+o" and target == botconfig.NICK and var.PHASE != "yok":
                decorators.unhook(HOOKS, 294)  # forget about it


    cli.who(botconfig.CHANNEL, "%nuhaf")


def mass_mode(cli, md):
    """ Example: mass_mode(cli, (('+v', 'asdf'), ('-v','wobosd'))) """
    lmd = len(md)  # store how many mode changes to do
    for start_i in range(0, lmd, 4):  # 4 mode-changes at a time
        if start_i + 4 > lmd:  # If this is a remainder (mode-changes < 4)
            z = list(zip(*md[start_i:]))  # zip this remainder
            ei = lmd % 4  # len(z)
        else:
            z = list(zip(*md[start_i:start_i+4])) # zip four
            ei = 4 # len(z)
        # Now z equal something like [('+v', '-v'), ('asdf', 'wobosd')]
        arg1 = "".join(z[0])
        arg2 = " ".join(z[1])  # + " " + " ".join([x+"!*@*" for x in z[1]])
        cli.mode(botconfig.CHANNEL, arg1, arg2)
        
def pm(cli, target, message):  # message either privmsg or notice, depending on user settings
    if target in var.USERS and var.USERS[target]["cloak"] in var.SIMPLE_NOTIFY:
        cli.notice(target, message)
    else:
        cli.msg(target, message)

def reset_settings():
    for attr in list(var.ORIGINAL_SETTINGS.keys()):
        setattr(var, attr, var.ORIGINAL_SETTINGS[attr])
    dict.clear(var.ORIGINAL_SETTINGS)

def reset_modes_timers(cli):
    # Reset game timers
    for x, timr in var.TIMERS.items():
        timr.cancel()
    var.TIMERS = {}

    # Reset modes
    cli.mode(botconfig.CHANNEL, "-m")
    cmodes = []
    for plr in var.list_players():
        cmodes.append(("-v", plr))
    for deadguy in var.DEAD:
        cmodes.append(("-q", deadguy+"!*@*"))
    mass_mode(cli, cmodes)

def reset(cli):
    var.PHASE = "yok"
    
    var.GAME_ID = 0

    var.DEAD = []

    var.ROLES = {"kisi" : []}

    var.JOINED_THIS_GAME = []

    reset_settings()

    dict.clear(var.LAST_SAID_TIME)
    dict.clear(var.PLAYERS)
    dict.clear(var.DCED_PLAYERS)
    dict.clear(var.DISCONNECTED)

def make_stasis(nick, penalty):
    try:
        cloak = var.USERS[nick]['cloak']
        if cloak is not None:
            var.STASISED[cloak] += penalty
    except KeyError:
        pass

@pmcmd("fdie", "fbye", admin_only=True)
@cmd("fdie", "fbye", admin_only=True)
def forced_exit(cli, nick, *rest):  # Admin Only
    """Forces the bot to close"""
    
    if var.PHASE in ("gunduz", "gece"):
        stop_game(cli)
    else:
        reset_modes_timers(cli)
        reset(cli)

    cli.quit(nick + " tarafindan zorla kapatildi.")



@pmcmd("frestart", admin_only=True)
@cmd("frestart", admin_only=True)
def restart_program(cli, nick, *rest):
    """Restarts the bot."""
    try:
        if var.PHASE in ("gunduz", "gece"):
            stop_game(cli)
        else:
            reset_modes_timers(cli)
            reset(cli)

        cli.quit(nick + " tarafindan yeniden basladi")
        raise SystemExit
    finally:
        print("RESTARTING")
        python = sys.executable
        if rest[-1].strip().lower() == "debugmode":
            os.execl(python, python, sys.argv[0], "--debug")
        elif rest[-1].strip().lower() == "normalmode":
            os.execl(python, python, sys.argv[0])
        elif rest[-1].strip().lower() == "verbosemode":
            os.execl(python, python, sys.argv[0], "--verbose")
        else:
            os.execl(python, python, *sys.argv)
    
            

@pmcmd("ping")
def pm_ping(cli, nick, rest):
    pm(cli, nick, 'Pong!')


@cmd("ping")
def pinger(cli, nick, chan, rest):
    """Pings the channel to get people's attention.  Rate-Limited."""

    if var.PHASE in ('gunduz','gece'):
        #cli.notice(nick, "You cannot use this command while a game is running.")
        cli.notice(nick, 'Pong!')
        return

    if (var.LAST_PING and
        var.LAST_PING + timedelta(seconds=var.PING_WAIT) > datetime.now()):
        cli.notice(nick, ("Bu komut limitlidir. " +
                          "Kullanmadan once biraz bekleyin."))
        return
        
    var.LAST_PING = datetime.now()
    if var.PINGING:
        return
    var.PINGING = True
    TO_PING = []



    @hook("whoreply", hookid=800)
    def on_whoreply(cli, server, dunno, chan, dunno1,
                    cloak, dunno3, user, status, dunno4):
        if not var.PINGING: return
        if user in (botconfig.NICK, nick): return  # Don't ping self.

        if (all((not var.OPT_IN_PING,
                 'G' not in status,  # not /away
                 '+' not in status,  # not already joined (voiced)
                 cloak not in var.STASISED, # not in stasis
                 cloak not in var.AWAY)) or
            all((var.OPT_IN_PING, '+' not in status,
                 cloak in var.PING_IN))):

            TO_PING.append(user)


    @hook("endofwho", hookid=800)
    def do_ping(*args):
        if not var.PINGING: return

        TO_PING.sort(key=lambda x: x.lower())
        
        cli.msg(botconfig.CHANNEL, "ALOO! "+" ".join(TO_PING))
        var.PINGING = False
 
        minimum = datetime.now() + timedelta(seconds=var.PING_MIN_WAIT)
        if not var.CAN_START_TIME or var.CAN_START_TIME < minimum:
           var.CAN_START_TIME = minimum

        decorators.unhook(HOOKS, 800)

    cli.who(botconfig.CHANNEL)


@cmd("basit", raw_nick = True)
@pmcmd("basit", raw_nick = True)
def mark_simple_notify(cli, nick, *rest):
    """If you want the bot to NOTICE you for every interaction"""
    
    nick, _, __, cloak = parse_nick(nick)
    
    if cloak in var.SIMPLE_NOTIFY:
        var.SIMPLE_NOTIFY.remove(cloak)
        var.remove_simple_rolemsg(cloak)
        
        cli.notice(nick, "Artik basit rol aciklamalari almayacaksin.")
        return
        
    var.SIMPLE_NOTIFY.append(cloak)
    var.add_simple_rolemsg(cloak)
    
    cli.notice(nick, "Artik basit rol aciklamalari alacaksin.")

if not var.OPT_IN_PING:
    @cmd("disarida", raw_nick=True)
    @pmcmd("disarida", raw_nick=True)
    def away(cli, nick, *rest):
        """Use this to activate your away status (so you aren't pinged)."""
        nick, _, _, cloak = parse_nick(nick)
        if cloak in var.AWAY:
            var.AWAY.remove(cloak)
            var.remove_away(cloak)

            cli.notice(nick, "Artik disarida olarak isaretli degilsin.")
            return
        var.AWAY.append(cloak)
        var.add_away(cloak)

        cli.notice(nick, "Simdi disarida olarak isaretlendin.")

    @cmd("geldim", raw_nick=True)
    @pmcmd("geldim", raw_nick=True)
    def back_from_away(cli, nick, *rest):
        """Unmarks away status"""
        nick, _, _, cloak = parse_nick(nick)
        if cloak not in var.AWAY:
            cli.notice(nick, "Disarida olarak isaretli degilsin.")
            return
        var.AWAY.remove(cloak)
        var.remove_away(cloak)

        cli.notice(nick, "Artik disarida olarak isaretli degilsin.")


else:  # if OPT_IN_PING setting is on
    @cmd("in", raw_nick=True)
    @pmcmd("in", raw_nick=True)
    def get_in(cli, nick, *rest):
        """Get yourself in the ping list"""
        nick, _, _, cloak = parse_nick(nick)
        if cloak in var.PING_IN:
            cli.notice(nick, "Zaten hatirlatma listesindesin")
            return
        var.PING_IN.append(cloak)
        var.add_ping(cloak)

        cli.notice(nick, "Artik hatirlatma listedesin.")

    @cmd("out", raw_nick=True)
    @pmcmd("out", raw_nick=True)
    def get_out(cli, nick, *rest):
        """Removes yourself from the ping list"""
        nick, _, _, cloak = parse_nick(nick)
        if cloak in var.PING_IN:
            var.PING_IN.remove(cloak)
            var.remove_ping(cloak)

            cli.notice(nick, "Artik hatirlatma listesinde degilsin.")
            return
        cli.notice(nick, "Hatirlatma listesinde degilsin.")


@cmd("fping", admin_only=True)
def fpinger(cli, nick, chan, rest):
    var.LAST_PING = None
    pinger(cli, nick, chan, rest)


@cmd("oyna", "o", raw_nick=True)
def join(cli, nick, chann_, rest):
    """Either starts a new game of Werewolf or joins an existing game that has not started yet."""
    pl = var.list_players()
    
    chan = botconfig.CHANNEL
    
    nick, _, __, cloak = parse_nick(nick)

    try:
        cloak = var.USERS[nick]['cloak']
        if cloak is not None and cloak in var.STASISED:
            cli.notice(nick, "{0} oyunluk uzaklastirildin.".format(var.STASISED[cloak]))
            return
    except KeyError:
        cloak = None
    

    if var.PHASE == "yok":
    
        cli.mode(chan, "+v", nick)
        var.ROLES["kisi"].append(nick)
        var.PHASE = "oyun"
        var.WAITED = 0
        var.GAME_ID = time.time()
        var.JOINED_THIS_GAME.append(cloak)
        var.CAN_START_TIME = datetime.now() + timedelta(seconds=var.MINIMUM_WAIT)
        cli.msg(chan, ('\u0002{0}\u0002 Kurtadam oyununu baslatti. '+
                      'Oynamak icin "{1}oyna" yazin. Oyunu baslatmak icin "{1}basla" yazin. '+
                      'Bekleme suresini artirmak icin "{1}bekle" yazin.').format(nick, botconfig.CMD_CHAR))
        
        # Set join timer
        if var.JOIN_TIME_LIMIT:
            t = threading.Timer(var.JOIN_TIME_LIMIT, kill_join, [cli, chan])
            var.TIMERS['oyna'] = t
            t.daemon = True
            t.start()
        
    elif nick in pl:
        cli.notice(nick, "Zaten oynuyorsun!")
    elif len(pl) >= var.MAX_PLAYERS:
        cli.notice(nick, "Cok fazla oyuncu var! Daha sonra tekrar dene.")
    elif var.PHASE != "oyun":
        cli.notice(nick, "Oyun zaten oynaniyor, sonra tekrar dene.")
    else:
    
        cli.mode(chan, "+v", nick)
        var.ROLES["kisi"].append(nick)
        cli.msg(chan, '\u0002{0}\u0002 da oyuna katildi ve oyun \u0002{1}\u0002 kisi oldu.'.format(nick, len(pl) + 1))
        if not cloak in var.JOINED_THIS_GAME:
            # make sure this only happens once
            var.JOINED_THIS_GAME.append(cloak)
            now = datetime.now()

            # add var.EXTRA_WAIT_JOIN to wait time
            if now > var.CAN_START_TIME:
                var.CAN_START_TIME = now + timedelta(seconds=var.EXTRA_WAIT_JOIN)
            else:
                var.CAN_START_TIME += timedelta(seconds=var.EXTRA_WAIT_JOIN)

            # make sure there's at least var.WAIT_AFTER_JOIN seconds of wait time left, if not add them
            if now + timedelta(seconds=var.WAIT_AFTER_JOIN) > var.CAN_START_TIME:
                var.CAN_START_TIME = now + timedelta(seconds=var.WAIT_AFTER_JOIN)

        var.LAST_STATS = None # reset
        var.LAST_GSTATS = None
        var.LAST_PSTATS = None
        var.LAST_TIME = None

        
def kill_join(cli, chan):
    pl = var.list_players()
    pl.sort(key=lambda x: x.lower())
    msg = 'PING! {0}'.format(", ".join(pl))
    reset_modes_timers(cli)
    reset(cli)
    cli.msg(chan, msg)
    cli.msg(chan, 'Oyunun baslamasi cok uzun surdu ve oyun iptal oldu. ' +
                  'Eger buralardaysan yeniden katilip yeni ' +
                  'bir oyun baslatin.')
    var.LOGGER.logMessage('Oyun iptal.')
    

@cmd("oynat", admin_only=True)
def fjoin(cli, nick, chann_, rest):
    noticed = False
    chan = botconfig.CHANNEL
    if not rest.strip():
        join(cli, nick, chan, "")

    for a in re.split(" +",rest):
        a = a.strip()
        if not a:
            continue
        ul = list(var.USERS.keys())
        ull = [u.lower() for u in ul]
        if a.lower() not in ull:
            if not is_fake_nick(a) or not botconfig.DEBUG_MODE:
                if not noticed:  # important
                    cli.msg(chan, nick+(": Sadece bu kanaldaki kisileri "+
                                        "oynatabilirsiniz."))
                    noticed = True
                continue
        if not is_fake_nick(a):
            a = ul[ull.index(a.lower())]
        if a != botconfig.NICK:
            join(cli, a.strip(), chan, "")
        else:
            cli.notice(nick, "hayir, bu yasak.")

@cmd("kov", "cikart", admin_only=True)
def fleave(cli, nick, chann_, rest):
    chan = botconfig.CHANNEL
    
    if var.PHASE == "yok":
        cli.notice(nick, "su an oyun oynanmiyor.")
    for a in re.split(" +",rest):
        a = a.strip()
        if not a:
            continue
        pl = var.list_players()
        pll = [x.lower() for x in pl]
        if a.lower() in pll:
            a = pl[pll.index(a.lower())]
        else:
            cli.msg(chan, nick+": bu kisi oynamiyor.")
            return
        cli.msg(chan, ("\u0002{0}\u0002, "+
                       " \u0002{1}\u0002 oyuncusunu cikmaya zorluyor.").format(nick, a))
        cli.msg(chan, "\02{0}\02 oyuncusuna elveda diyin.".format(var.get_role(a)))
        if var.PHASE == "oyun":
            cli.msg(chan, ("Yeni oyuncu sayisi: \u0002{0}\u0002").format(len(var.list_players()) - 1))
        if var.PHASE in ("gunduz", "gece"):
            var.LOGGER.logMessage("{0}, {1} oyuncusunu cikmaya zorluyor.".format(nick, a))
            var.LOGGER.logMessage("{0} oyuncusuna elveda diyin".format(var.get_role(a)))
        del_player(cli, a)


@cmd("baslat", admin_only=True)
def fstart(cli, nick, chan, rest):
    var.CAN_START_TIME = datetime.now()
    cli.msg(botconfig.CHANNEL, "\u0002{0}\u0002 oyunu baslamaya zorluyor.".format(nick))
    start(cli, nick, chan, rest)



@hook("kick")
def on_kicked(cli, nick, chan, victim, reason):
    if victim == botconfig.NICK:
        cli.join(botconfig.CHANNEL)
        cli.msg("ChanServ", "op "+botconfig.CHANNEL)


@hook("account")
def on_account(cli, nick, acc):
    nick, mode, user, cloak = parse_nick(nick)
    if nick in var.USERS.keys():
        var.USERS[nick]["cloak"] = cloak
        var.USERS[nick]["account"] = acc

@cmd("durum")
def stats(cli, nick, chan, rest):
    """Display the player statistics"""
    if var.PHASE == "yok":
        cli.notice(nick, "su an oyun oynanmiyor.")
        return
        
    pl = var.list_players()
    
    if nick != chan and (nick in pl or var.PHASE == "oyun"):
        # only do this rate-limiting stuff if the person is in game
        if (var.LAST_STATS and
            var.LAST_STATS + timedelta(seconds=var.STATS_RATE_LIMIT) > datetime.now()):
            cli.notice(nick, ("bu komutun kullanimi kisitlidir. " +
                              "kullanmadan once biraz bekle."))
            return
            
        var.LAST_STATS = datetime.now()
    
    pl.sort(key=lambda x: x.lower())
    if len(pl) > 1:
        msg = '{0}: \u0002{1}\u0002 oyuncular: {2}'.format(nick,
            len(pl), ", ".join(pl))
    else:
        msg = '{0}: \u00021\u0002 oyuncu: {1}'.format(nick, pl[0])
    
    if nick == chan:
        pm(cli, nick, msg)
    else:
        if nick in pl or var.PHASE == "oyun":
            cli.msg(chan, msg)
            var.LOGGER.logMessage(msg.replace("\02", ""))
        else:
            cli.notice(nick, msg)
        
    if var.PHASE == "oyun":
        return

    message = []
    f = False  # set to true after the is/are verb is decided
    l1 = [k for k in var.ROLES.keys()
          if var.ROLES[k]]
    l2 = [k for k in var.ORIGINAL_ROLES.keys()
          if var.ORIGINAL_ROLES[k]]
    rs = list(set(l1+l2))
        
    # Due to popular demand, picky ordering
    if "kurt" in rs:
        rs.remove("kurt")
        rs.insert(0, "kurt")
    if "gozcu" in rs:
        rs.remove("gozcu")
        rs.insert(1, "gozcu")
    if "koylu" in rs:
        rs.remove("koylu")
        rs.append("koylu")
        
        
    firstcount = len(var.ROLES[rs[0]])
    if firstcount > 1 or not firstcount:
        vb = "are"
    else:
        vb = "is"
    for role in rs:
        count = len(var.ROLES[role])
        if role == "hain" and var.HIDDEN_TRAITOR:
            continue
        elif role == "koylu" and var.HIDDEN_TRAITOR:
            count += len(var.ROLES["hain"])
                
        if count > 1 or count == 0:
            message.append("\u0002{0}\u0002 {1}".format(count if count else "\u0002no\u0002", var.plural(role)))
        else:
            message.append("\u0002{0}\u0002 {1}".format(count, role))
    stats_mssg =  "{0}: su an {4}. {1} ve {2} var.".format(nick,
                                                        ", ".join(message[0:-1]),
                                                        message[-1],
                                                        vb,
                                                        var.PHASE)
    if nick == chan:
        pm(cli, nick, stats_mssg)
    else:
        if nick in pl or var.PHASE == "oyun":
            cli.msg(chan, stats_mssg)
            var.LOGGER.logMessage(stats_mssg.replace("\02", ""))
        else:
            cli.notice(nick, stats_mssg)

@pmcmd("durum")
def stats_pm(cli, nick, rest):
    stats(cli, nick, nick, rest)



def hurry_up(cli, gameid, change):
    if var.PHASE != "gunduz": return
    if gameid:
        if gameid != var.DAY_ID:
            return

    chan = botconfig.CHANNEL
    
    if not change:
        cli.msg(chan, ("\02Gunes yavas yavas ufukta kaybolup giderken, ufukta gorunune cam agaclari " +
                      "belli belirsiz ve karanlik siluetlere donusurken, koyluler karar vermek icin cok az bir zamanlari " +
                      "kaldigini biliyorlar; eger karanlik coker ve ayisigi koyu aydinlatirsa " +
                      "cogunluk oylamayi kazanacak. Eger hic oy yoksa ve oylar esitse kimse linc " +
                      "edilmeyecek.\02"))
        if not var.DAY_TIME_LIMIT_CHANGE:
            return
        if (len(var.list_players()) <= var.SHORT_DAY_PLAYERS):
            tmr = threading.Timer(var.SHORT_DAY_LIMIT_CHANGE, hurry_up, [cli, var.DAY_ID, True])
        else:
            tmr = threading.Timer(var.DAY_TIME_LIMIT_CHANGE, hurry_up, [cli, var.DAY_ID, True])
        tmr.daemon = True
        var.TIMERS["gunduz"] = tmr
        tmr.start()
        return
        
    
    var.DAY_ID = 0
    
    pl = var.list_players()
    avail = len(pl) - len(var.WOUNDED)
    votesneeded = avail // 2 + 1

    found_dup = False
    maxfound = (0, "")
    for votee, voters in iter(var.VOTES.items()):
        if len(voters) > maxfound[0]:
            maxfound = (len(voters), votee)
            found_dup = False
        elif len(voters) == maxfound[0]:
            found_dup = True
    if maxfound[0] > 0 and not found_dup:
        cli.msg(chan, "Gunes batar.")
        var.LOGGER.logMessage("Gunes batar.")
        var.VOTES[maxfound[1]] = [None] * votesneeded
        chk_decision(cli)  # Induce a lynch
    else:
        cli.msg(chan, ("Gunes batarken, koyluler yataklarina donup "+
                      "sabahi beklemeye karar verdiler."))
        var.LOGGER.logMessage(("Gunes batarken, koyluler yataklarina donup "+
                               "sabahi beklemeye karar verdiler."))
        transition_night(cli)
        



@cmd("gece", admin_only=True)
def fnight(cli, nick, chan, rest):
    if var.PHASE != "gunduz":
        cli.notice(nick, "Gunduz degil.")
    else:
        hurry_up(cli, 0, True)


@cmd("gunduz", admin_only=True)
def fday(cli, nick, chan, rest):
    if var.PHASE != "gece":
        cli.notice(nick, "Gece degil.")
    else:
        transition_day(cli)



def chk_decision(cli):
    chan = botconfig.CHANNEL
    pl = var.list_players()
    avail = len(pl) - len(var.WOUNDED)
    votesneeded = avail // 2 + 1
    for votee, voters in iter(var.VOTES.items()):
        if len(voters) >= votesneeded:
            lmsg = random.choice(var.LYNCH_MESSAGES).format(votee, var.get_reveal_role(votee))
            cli.msg(botconfig.CHANNEL, lmsg)
            var.LOGGER.logMessage(lmsg.replace("\02", ""))
            var.LOGGER.logBare(votee, "LYNCHED")
            if del_player(cli, votee, True):
                transition_night(cli)



@cmd("oylar")
def show_votes(cli, nick, chan, rest):
    """Displays the voting statistics."""
    
    if var.PHASE in ("yok", "oyun"):
        cli.notice(nick, "su an bir oyun oynanmiyor.")
        return
    if var.PHASE != "gunduz":
        cli.notice(nick, "oylama sadece gunduzleri yapilir.")
        return
    
    if (var.LAST_VOTES and
        var.LAST_VOTES + timedelta(seconds=var.VOTES_RATE_LIMIT) > datetime.now()):
        cli.notice(nick, ("bu komut limitlidir." +
                          "lutfen kullanmadan once biraz bekle."))
        return    
    
    pl = var.list_players()
    
    if nick in pl:
        var.LAST_VOTES = datetime.now()    
        
    if not var.VOTES.values():
        msg = nick+": su an hic oy yok."
        if nick in pl:
            var.LAST_VOTES = None # reset
    else:
        votelist = ["{0}: {1} ({2})".format(votee,
                                            len(var.VOTES[votee]),
                                            " ".join(var.VOTES[votee]))
                    for votee in var.VOTES.keys()]
        msg = "{0}: {1}".format(nick, ", ".join(votelist))
        
    if nick in pl:
        cli.msg(chan, msg)
    else:
        cli.notice(nick, msg)

    pl = var.list_players()
    avail = len(pl) - len(var.WOUNDED)
    votesneeded = avail // 2 + 1
    the_message = ("{0}: \u0002{1}\u0002 oyuncu, linc icin \u0002{2}\u0002 oy "+
                   "gerekiyor, \u0002{3}\u0002 oylama yapabilir" +
                   ".").format(nick, len(pl), votesneeded, avail)
    if nick in pl:
        cli.msg(chan, the_message)
    else:
        cli.notice(nick, the_message)



def chk_traitor(cli):
    for tt in var.ROLES["traitor"]:
        var.ROLES["kurt"].append(tt)
        var.ROLES["hain"].remove(tt)
        pm(cli, tt, ('AUUUUUU. Simdi kurta donustun!!\n'+
                     'istersen intikamini alabilirsin!'))



def stop_game(cli, winner = ""):
    chan = botconfig.CHANNEL
    if var.DAY_START_TIME:
        now = datetime.now()
        td = now - var.DAY_START_TIME
        var.DAY_TIMEDELTA += td
    if var.NIGHT_START_TIME:
        now = datetime.now()
        td = now - var.NIGHT_START_TIME
        var.NIGHT_TIMEDELTA += td

    daymin, daysec = var.DAY_TIMEDELTA.seconds // 60, var.DAY_TIMEDELTA.seconds % 60
    nitemin, nitesec = var.NIGHT_TIMEDELTA.seconds // 60, var.NIGHT_TIMEDELTA.seconds % 60
    total = var.DAY_TIMEDELTA + var.NIGHT_TIMEDELTA
    tmin, tsec = total.seconds // 60, total.seconds % 60
    gameend_msg = ("Oyun bitti \u0002{0:0>2}:{1:0>2}\u0002. " +
                   "\u0002{2:0>2}:{3:0>2}\u0002 gunduzdu. " +
                   "\u0002{4:0>2}:{5:0>2}\u0002 geceydi. ").format(tmin, tsec,
                                                                     daymin, daysec,
                                                                     nitemin, nitesec)
    cli.msg(chan, gameend_msg)
    var.LOGGER.logMessage(gameend_msg.replace("\02", "")+"\n")
    var.LOGGER.logBare("DAY", "TIME", str(var.DAY_TIMEDELTA.seconds))
    var.LOGGER.logBare("NIGHT", "TIME", str(var.NIGHT_TIMEDELTA.seconds))
    var.LOGGER.logBare("GAME", "TIME", str(total.seconds))

    roles_msg = []
    
    var.ORIGINAL_ROLES["lanetli koylu"] = var.CURSED  # A hack
    var.ORIGINAL_ROLES["avci"] = list(var.GUNNERS.keys())

    lroles = list(var.ORIGINAL_ROLES.keys())
    lroles.remove("kurt")
    lroles.insert(0, "kurt")   # picky, howl consistency
    
    for role in lroles:
        if len(var.ORIGINAL_ROLES[role]) == 0 or role == "koylu":
            continue
        playersinrole = list(var.ORIGINAL_ROLES[role])
        for i,plr in enumerate(playersinrole):
            if plr.startswith("(dced)"):  # don't care about it here
                playersinrole[i] = plr[6:]
        if len(playersinrole) == 2:
            msg = "{1} \u0002{0[0]}\u0002 ve \u0002{0[1]}\u0002 idi."
            roles_msg.append(msg.format(playersinrole, var.plural(role)))
        elif len(playersinrole) == 1:
            roles_msg.append("{1} \u0002{0[0]}\u0002 idi.".format(playersinrole,
                                                                      role))
        else:
            msg = "{2} {0}, ve \u0002{1}\u0002 idi."
            nickslist = ["\u0002"+x+"\u0002" for x in playersinrole[0:-1]]
            roles_msg.append(msg.format(", ".join(nickslist),
                                                  playersinrole[-1],
                                                  var.plural(role)))
    cli.msg(chan, " ".join(roles_msg))

    reset_modes_timers(cli)
    
    # Set temporary phase to deal with disk lag
    var.PHASE = "kaydediliyor"
    
    plrl = []
    for role,ppl in var.ORIGINAL_ROLES.items():
        for x in ppl:
            plrl.append((x, role))
    
    var.LOGGER.saveToFile()
    
    for plr, rol in plrl:
        #if plr not in var.USERS.keys():  # they died TODO: when a player leaves, count the game as lost for them
        #    if plr in var.DEAD_USERS.keys():
        #        acc = var.DEAD_USERS[plr]["account"]
        #    else:
        #        continue  # something wrong happened
        #else:
        if plr.startswith("(dced)") and plr[6:] in var.DCED_PLAYERS.keys():
            acc = var.DCED_PLAYERS[plr[6:]]["account"]
        elif plr in var.PLAYERS.keys():
            acc = var.PLAYERS[plr]["account"]
        else:
            continue  #probably fjoin'd fake

        if acc == "*":
            continue  # not logged in during game start
        # determine if this player's team won
        if plr in (var.ORIGINAL_ROLES["kurt"] + var.ORIGINAL_ROLES["hain"] +
                   var.ORIGINAL_ROLES["karga"]):  # the player was wolf-aligned
            if winner == "kurtlar":
                won = True
            elif winner == "koyluler":
                won = False
            else:
                break  # abnormal game stop
        else:
            if winner == "kurtlar":
                won = False
            elif winner == "koyluler":
                won = True
            else:
                break
                
        iwon = won and plr in var.list_players()  # survived, team won = individual win
                
        var.update_role_stats(acc, rol, won, iwon)
    
    size = len(var.list_players()) + len(var.DEAD)
    if winner != "": # Only update if not an abnormal game stop
        var.update_game_stats(size, winner)
    
    reset(cli)
    
    # This must be after reset(cli)
    if var.AFTER_FLASTGAME:
        var.AFTER_FLASTGAME()
        var.AFTER_FLASTGAME = None
    if var.ADMIN_TO_PING:  # It was an flastgame
        cli.msg(chan, "PING! " + var.ADMIN_TO_PING)
        var.ADMIN_TO_PING = None
    
    return True

def chk_win(cli, end_game = True):
    """ Returns True if someone won """
    
    chan = botconfig.CHANNEL
    lpl = len(var.list_players())
    
    if lpl == 0:
        #cli.msg(chan, "No more players remaining. Game ended.")
        reset_modes_timers(cli)
        reset(cli)
        return True
        
    if var.PHASE == "oyun":
        return False
        
        
    lwolves = (len(var.ROLES["kurt"])+
               len(var.ROLES["hain"])+
               len(var.ROLES["karga"]))
    if var.PHASE == "gunduz":
        lpl -= len([x for x in var.WOUNDED if x not in var.ROLES["hain"]])
        lwolves -= len([x for x in var.WOUNDED if x in var.ROLES["hain"]])
    
    if lwolves == lpl / 2:
        message = ("OYUN BITTI! Kurtlar ve koyluler ayni sayidaydi. " +
                  "Kurtlar daha guclu olduklarindan kazandilar.")
        village_win = False
    elif lwolves > lpl / 2:
        message = ("OYUN BITTI! Kurtlar daha fazlaydi. "+
                  "Ve kurtlar kazandilar.")
        village_win = False
    elif (not var.ROLES["kurt"] and
          not var.ROLES["hain"] and
          not var.ROLES["karga"]):
        message = ("OYUN BITTI! Tum kurtlar olduruldu. Koyluler " +
                  "kurtlari ibret olsun diye koyun girisine astilar.")
        village_win = True
    elif (not var.ROLES["kurt"] and not 
          var.ROLES["karga"] and var.ROLES["hain"]):
        for t in var.ROLES["hain"]:
            var.LOGGER.logBare(t, "TRANSFORM")
        chk_traitor(cli)
        cli.msg(chan, ('\u0002Koyluler kutlama sirasinda bir kurt ulumasi duydular '+
                       've irkildiler! Kurtlar olmemis miydi?! '+
                       '\u0002'))
        var.LOGGER.logMessage(('The villagers, during their celebrations, are '+
                               'frightened as they hear a loud howl. The wolves are '+
                               'not gone!'))
        return chk_win(cli, end_game)
    else:
        return False
    if end_game:
        cli.msg(chan, message)
        var.LOGGER.logMessage(message)
        var.LOGGER.logBare("VILLAGERS" if village_win else "WOLVES", "WIN")
        stop_game(cli, "koyluler" if village_win else "kurtlar")
    return True





def del_player(cli, nick, forced_death = False, devoice = True, end_game = True):
    """
    Returns: False if one side won.
    arg: forced_death = True when lynched or when the seer/wolf both don't act
    """
    t = time.time()  #  time
    
    var.LAST_STATS = None # reset
    var.LAST_VOTES = None
    
    with var.GRAVEYARD_LOCK:
        if not var.GAME_ID or var.GAME_ID > t:
            #  either game ended, or a new game has started.
            return False
        cmode = []
        if devoice:
            cmode.append(("-v", nick))
        var.del_player(nick)
        ret = True
        if var.PHASE == "oyun":
            # Died during the joining process as a person
            mass_mode(cli, cmode)
            return not chk_win(cli)
        if var.PHASE != "oyun":
            # Died during the game, so quiet!
            if not is_fake_nick(nick):
                cmode.append(("+q", nick+"!*@*"))
            mass_mode(cli, cmode)
            if nick not in var.DEAD:
                var.DEAD.append(nick)
            ret = not chk_win(cli, end_game)
        if var.PHASE in ("gece", "gunduz") and ret:
            # remove the player from variables if they're in there
            for a,b in list(var.KILLS.items()):
                if b == nick:
                    del var.KILLS[a]
                elif a == nick:
                    del var.KILLS[a]
            for x in (var.OBSERVED, var.HVISITED, var.GUARDED):
                keys = list(x.keys())
                for k in keys:
                    if k == nick:
                        del x[k]
                    elif x[k] == nick:
                        del x[k]
            if nick in var.DISCONNECTED:
                del var.DISCONNECTED[nick]
        if var.PHASE == "gunduz" and not forced_death and ret:  # didn't die from lynching
            if nick in var.VOTES.keys():
                del var.VOTES[nick]  #  Delete other people's votes on the player
            for k in list(var.VOTES.keys()):
                if nick in var.VOTES[k]:
                    var.VOTES[k].remove(nick)
                    if not var.VOTES[k]:  # no more votes on that person
                        del var.VOTES[k]
                    break # can only vote once
                    
            if nick in var.WOUNDED:
                var.WOUNDED.remove(nick)
            chk_decision(cli)
        elif var.PHASE == "gece" and ret:
            chk_nightdone(cli)
        return ret  


def reaper(cli, gameid):
    # check to see if idlers need to be killed.
    var.IDLE_WARNED = []
    chan = botconfig.CHANNEL
    
    while gameid == var.GAME_ID:
        with var.GRAVEYARD_LOCK:
            # Terminate reaper when experiencing disk lag
            if var.PHASE == "kaydediliyor":
                return
            if var.WARN_IDLE_TIME or var.KILL_IDLE_TIME:  # only if enabled
                to_warn = []
                to_kill = []
                for nick in var.list_players():
                    lst = var.LAST_SAID_TIME.get(nick, var.GAME_START_TIME)
                    tdiff = datetime.now() - lst
                    if (tdiff > timedelta(seconds=var.WARN_IDLE_TIME) and
                                            nick not in var.IDLE_WARNED):
                        if var.WARN_IDLE_TIME:
                            to_warn.append(nick)
                        var.IDLE_WARNED.append(nick)
                        var.LAST_SAID_TIME[nick] = (datetime.now() -
                            timedelta(seconds=var.WARN_IDLE_TIME))  # Give them a chance
                    elif (tdiff > timedelta(seconds=var.KILL_IDLE_TIME) and
                        nick in var.IDLE_WARNED):
                        if var.KILL_IDLE_TIME:
                            to_kill.append(nick)
                    elif (tdiff < timedelta(seconds=var.WARN_IDLE_TIME) and
                        nick in var.IDLE_WARNED):
                        var.IDLE_WARNED.remove(nick)  # player saved himself from death
                for nck in to_kill:
                    if nck not in var.list_players():
                        continue
                    cli.msg(chan, ("\u0002{0}\u0002 yatagindan cok uzun sure kalkmayinca "+
                                   "olu olarak bulundu. Tum koy halki \u0002{1}\u0002 cesedini "+
                                   "gomduler.").format(nck, var.get_reveal_role(nck)))
                    make_stasis(nck, var.IDLE_STASIS_PENALTY)
                    if not del_player(cli, nck):
                        return
                pl = var.list_players()
                x = [a for a in to_warn if a in pl]
                if x:
                    cli.msg(chan, ("{0}: \u0002uzun zamandir tartismalara katilmiyorsun. "+
                                   "bir seyler soylemezsen "+
                                   "olmus olacaksin.\u0002").format(", ".join(x)))
            for dcedplayer in list(var.DISCONNECTED.keys()):
                _, timeofdc, what = var.DISCONNECTED[dcedplayer]
                if what == "quit" and (datetime.now() - timeofdc) > timedelta(seconds=var.QUIT_GRACE_TIME):
                    cli.msg(chan, ("\02{0}\02 vahsi hayvanlarca parcalanarak olduruldu. Anlasilan "+
                                   "\02{1}\02 eti tatli oluyor.").format(dcedplayer, var.get_reveal_role(dcedplayer)))
                    if var.PHASE != "oyna":
                        make_stasis(dcedplayer, var.PART_STASIS_PENALTY)
                    if not del_player(cli, dcedplayer, devoice = False):
                        return
                elif what == "part" and (datetime.now() - timeofdc) > timedelta(seconds=var.PART_GRACE_TIME):
                    cli.msg(chan, ("\02{0}\02, bir \02{1}\02, bazi zehirli cileklerden yedi ve "+
                                   "oldu.").format(dcedplayer, var.get_reveal_role(dcedplayer)))
                    if var.PHASE != "oyun":
                        make_stasis(dcedplayer, var.PART_STASIS_PENALTY)
                    if not del_player(cli, dcedplayer, devoice = False):
                        return
        time.sleep(10)



@cmd("")  # update last said
def update_last_said(cli, nick, chan, rest):
    if var.PHASE not in ("oyun", "yok"):
        var.LAST_SAID_TIME[nick] = datetime.now()
    
    if var.PHASE not in ("yok", "oyun"):
        var.LOGGER.logChannelMessage(nick, rest)

    fullstring = "".join(rest)
    if var.CARE_BOLD and BOLD in fullstring:
        if var.KILL_BOLD:
            cli.send("KICK {0} {1} :Bold kullanamazsin".format(botconfig.CHANNEL, nick))
        else:
            cli.notice(nick, "Kanalda bold kullanmak yasaktir.")
    if var.CARE_COLOR and any(code in fullstring for code in ["\x03", "\x16", "\x1f" ]):
        if var.KILL_COLOR:
            cli.send("KICK {0} {1} :Renk kullanamazsin".format(botconfig.CHANNEL, nick))
        else:
            cli.notice(nick, "Kanalda renk kullanmak yasaktir.")

@hook("oyna")
def on_join(cli, raw_nick, chan, acc="*", rname=""):
    nick,m,u,cloak = parse_nick(raw_nick)
    if nick != botconfig.NICK:
        if nick not in var.USERS.keys():
            var.USERS[nick] = dict(cloak=cloak,account=acc)
        else:
            var.USERS[nick]["cloak"] = cloak
            var.USERS[nick]["account"] = acc
    with var.GRAVEYARD_LOCK:
        if nick in var.DISCONNECTED.keys():
            clk = var.DISCONNECTED[nick][0]
            if cloak == clk:
                cli.mode(chan, "+v", nick, nick+"!*@*")
                del var.DISCONNECTED[nick]
                var.LAST_SAID_TIME[nick] = datetime.now()
                cli.msg(chan, "\02{0}\02 koye geri dondu.".format(nick))
                for r,rlist in var.ORIGINAL_ROLES.items():
                    if "(dced)"+nick in rlist:
                        rlist.remove("(dced)"+nick)
                        rlist.append(nick)
                        break
                if nick in var.DCED_PLAYERS.keys():
                    var.PLAYERS[nick] = var.DCED_PLAYERS.pop(nick)

@cmd("keci")
def goat(cli, nick, chan, rest):
    """Use a goat to interact with anyone in the channel during the day"""
    if var.PHASE in ("yok", "oyun"):
        cli.notice(nick, "su an oyun oynanmiyor.")
        return
    elif nick not in var.list_players() or nick in var.DISCONNECTED.keys():
        cli.notice(nick, "su an oynamiyorsun.")
        return
    if var.PHASE != "gunduz":
        cli.notice(nick, "bunu sadece gunduz yapabilirsin.")
        return
    if var.GOATED and nick not in var.SPECIAL_ROLES["keci cobani"]:
        cli.notice(nick, "bunu sadece gunde bir kere yapabilirsin.")
        return
    ul = list(var.USERS.keys())
    ull = [x.lower() for x in ul]
    rest = re.split(" +",rest)[0].strip().lower()
    if not rest:
        cli.notice(nick, "eksik parametre.")
        return
    matches = 0
    for player in ull:
        if rest == player:
            victim = player
            break
        if player.startswith(rest):
            victim = player
            matches += 1
    else:
        if matches != 1:
            pm(cli, nick,"\u0002{0}\u0002 bu kanalda degil.".format(rest))
            return
    victim = ul[ull.index(victim)]
    goatact = random.choice(["tekmelendi", "boynuzlandi"])
    cli.msg(botconfig.CHANNEL, ("\u0002{0}\u0002 kecisi yurudu ve "+
                                "\u0002{2}\u0002 {1}'.").format(nick,
                                                                   goatact, victim))
    var.LOGGER.logMessage("{0}'s goat walks by and {1} {2}.".format(nick, goatact,
                                                                    victim))
    var.GOATED = True
    
    

@hook("nick")
def on_nick(cli, prefix, nick):
    prefix,u,m,cloak = parse_nick(prefix)
    chan = botconfig.CHANNEL

    if prefix in var.USERS:
        var.USERS[nick] = var.USERS.pop(prefix)
        
    if prefix == var.ADMIN_TO_PING:
        var.ADMIN_TO_PING = nick

    # for k,v in list(var.DEAD_USERS.items()):
        # if prefix == k:
            # var.DEAD_USERS[nick] = var.DEAD_USERS[k]
            # del var.DEAD_USERS[k]

    if prefix in var.list_players() and prefix not in var.DISCONNECTED.keys():
        r = var.ROLES[var.get_role(prefix)]
        r.append(nick)
        r.remove(prefix)

        if var.PHASE in ("gece", "gunduz"):
            for k,v in var.ORIGINAL_ROLES.items():
                if prefix in v:
                    var.ORIGINAL_ROLES[k].remove(prefix)
                    var.ORIGINAL_ROLES[k].append(nick)
                    break
            for k,v in list(var.PLAYERS.items()):
                if prefix == k:
                    var.PLAYERS[nick] = var.PLAYERS[k]
                    del var.PLAYERS[k]
            if prefix in var.GUNNERS.keys():
                var.GUNNERS[nick] = var.GUNNERS.pop(prefix)
            if prefix in var.CURSED:
                var.CURSED.append(nick)
                var.CURSED.remove(prefix)
            for dictvar in (var.HVISITED, var.OBSERVED, var.GUARDED, var.KILLS):
                kvp = []
                for a,b in dictvar.items():
                    if a == prefix:
                        a = nick
                    if b == prefix:
                        b = nick
                    kvp.append((a,b))
                dictvar.update(kvp)
                if prefix in dictvar.keys():
                    del dictvar[prefix]
            if prefix in var.SEEN:
                var.SEEN.remove(prefix)
                var.SEEN.append(nick)
            with var.GRAVEYARD_LOCK:  # to be safe
                if prefix in var.LAST_SAID_TIME.keys():
                    var.LAST_SAID_TIME[nick] = var.LAST_SAID_TIME.pop(prefix)
                if prefix in var.IDLE_WARNED:
                    var.IDLE_WARNED.remove(prefix)
                    var.IDLE_WARNED.append(nick)

        if var.PHASE == "gunduz":
            if prefix in var.WOUNDED:
                var.WOUNDED.remove(prefix)
                var.WOUNDED.append(nick)
            if prefix in var.INVESTIGATED:
                var.INVESTIGATED.remove(prefix)
                var.INVESTIGATED.append(prefix)
            if prefix in var.VOTES:
                var.VOTES[nick] = var.VOTES.pop(prefix)
            for v in var.VOTES.values():
                if prefix in v:
                    v.remove(prefix)
                    v.append(nick)

    # Check if he was DC'ed
    if var.PHASE in ("gece", "gunduz"):
        with var.GRAVEYARD_LOCK:
            if nick in var.DISCONNECTED.keys():
                clk = var.DISCONNECTED[nick][0]
                if cloak == clk:
                    cli.mode(chan, "+v", nick, nick+"!*@*")
                    del var.DISCONNECTED[nick]
                    
                    cli.msg(chan, ("\02{0}\02 koye geri "+
                                   "dondu.").format(nick))

def leave(cli, what, nick, why=""):
    nick, _, _, cloak = parse_nick(nick)

    if what == "part" and why != botconfig.CHANNEL: return
        
    if why and why == botconfig.CHANGING_HOST_QUIT_MESSAGE:
        return
    if var.PHASE == "yok":
        return
    if nick in var.PLAYERS:
        # must prevent double entry in var.ORIGINAL_ROLES
        for r,rlist in var.ORIGINAL_ROLES.items():
            if nick in rlist:
                var.ORIGINAL_ROLES[r].remove(nick)
                var.ORIGINAL_ROLES[r].append("(dced)"+nick)
                break
        var.DCED_PLAYERS[nick] = var.PLAYERS.pop(nick)
    if nick not in var.list_players() or nick in var.DISCONNECTED.keys():
        return
        
    #  the player who just quit was in the game
    killplayer = True
    if what == "part" and (not var.PART_GRACE_TIME or var.PHASE == "oyun"):
        msg = ("\02{0}\02, bir \02{1}\02, bazi zehirli cileklerden yedi ve "+
               "oldu.").format(nick, var.get_reveal_role(nick))
    elif what == "quit" and (not var.QUIT_GRACE_TIME or var.PHASE == "oyun"):
        msg = ("\02{0}\02 vahsi hayvanlarca parcalanarak olduruldu. Gorunen o ki "+
               "\02{1}\02 eti tatliymis.").format(nick, var.get_reveal_role(nick))
    elif what != "kick":
        msg = "\u0002{0}\u0002 kayboldu.".format(nick)
        killplayer = False
    else:
        msg = ("\02{0}\02 ucurumdan duserek yasamini yitirdi. Bir "+
               "\02{1}\02 sonsuzlukta kayboldu.").format(nick, var.get_reveal_role(nick))
        make_stasis(nick, var.LEAVE_STASIS_PENALTY)
    cli.msg(botconfig.CHANNEL, msg)
    var.LOGGER.logMessage(msg.replace("\02", ""))
    if killplayer:
        del_player(cli, nick)
    else:
        var.DISCONNECTED[nick] = (cloak, datetime.now(), what)

#Functions decorated with hook do not parse the nick by default
hook("part")(lambda cli, nick, *rest: leave(cli, "part", nick, rest[0]))
hook("quit")(lambda cli, nick, *rest: leave(cli, "quit", nick, rest[0]))
hook("kick")(lambda cli, nick, *rest: leave(cli, "kick", rest[1]))


@cmd("cik", "ayril", "q")
def leave_game(cli, nick, chan, rest):
    """Quits the game."""
    if var.PHASE == "yok":
        cli.notice(nick, "Su an bir oyun yok.")
        return
    elif var.PHASE == "oyun":
        lpl = len(var.list_players()) - 1

        if lpl == 0:
            population = (" baska oyuncu kalmadi.")
        else:
            population = (" yeni oyuncu sayisi: \u0002{0}\u0002").format(lpl)
    else:
        population = ""
    if nick not in var.list_players() or nick in var.DISCONNECTED.keys():  # not playing
        cli.notice(nick, "su an oynamiyorsun.")
        return
    cli.msg(botconfig.CHANNEL, ("\02{0}\02, bir \02{1}\02, bilinmez bir hastaliktan aci cekerek oldu.{2}").format(nick, var.get_reveal_role(nick), population))
    var.LOGGER.logMessage(("{0}, a {1}, has died of an unknown disease.").format(nick, var.get_reveal_role(nick)))
    if var.PHASE != "oyun":
        make_stasis(nick, var.LEAVE_STASIS_PENALTY)

    del_player(cli, nick)
    



def begin_day(cli):
    chan = botconfig.CHANNEL

    # Reset nighttime variables
    var.KILLS = {}  # nicknames of kill victim
    var.KILLER = ""  # nickname of who chose the victim
    var.SEEN = []  # list of seers that have had visions
    var.OBSERVED = {}  # those whom werecrows have observed
    var.HVISITED = {}
    var.GUARDED = {}
    var.STARTED_DAY_PLAYERS = len(var.list_players())
    
    msg = ("koyluler simdi oldurmek icin birini secmeli. "+
           '"{0}linc <nick>" yazarak oy kullanabilirsin. linc icin {1} oy '+
           'gerekli.').format(botconfig.CMD_CHAR, len(var.list_players()) // 2 + 1)
    cli.msg(chan, msg)
    var.LOGGER.logMessage(msg)
    var.LOGGER.logBare("DAY", "BEGIN")

    if var.DAY_TIME_LIMIT_WARN > 0:  # Time limit enabled
        var.DAY_ID = time.time()
        if var.STARTED_DAY_PLAYERS <= var.SHORT_DAY_PLAYERS:
            t = threading.Timer(var.SHORT_DAY_LIMIT_WARN, hurry_up, [cli, var.DAY_ID, False])
        else:
            t = threading.Timer(var.DAY_TIME_LIMIT_WARN, hurry_up, [cli, var.DAY_ID, False])
        var.TIMERS["day_warn"] = t
        t.daemon = True
        t.start()

def night_warn(cli, gameid):
    if gameid != var.NIGHT_ID:
        return
    
    if var.PHASE == "gunduz":
        return
        
    cli.msg(botconfig.CHANNEL, ("\02bir kac koylu erkenden uyandi ve disarisinin " +
                                "hala karanlik oldugunu farkettiler. " +
                                "gece neredeyse bitti, ve disaridan hala " +
                                "islik sesleri ve degisik sesler geliyor.\02"))

def transition_day(cli, gameid=0):
    if gameid:
        if gameid != var.NIGHT_ID:
            return
    var.NIGHT_ID = 0
    
    if var.PHASE == "gunduz":
        return
    
    var.PHASE = "gunduz"
    var.GOATED = False
    chan = botconfig.CHANNEL
    
    # Reset daytime variables
    var.VOTES = {}
    var.INVESTIGATED = []
    var.WOUNDED = []
    var.DAY_START_TIME = datetime.now()

    if (not len(var.SEEN)+len(var.KILLS)+len(var.OBSERVED) # neither seer nor wolf acted
            and not var.START_WITH_DAY and var.FIRST_NIGHT and var.ROLES["gozcu"] and not botconfig.DEBUG_MODE):
        cli.msg(botconfig.CHANNEL, "\02butun kurtlar gizli bir vebadan dolayi can verdiler.\02")
        for x in var.ROLES["kurt"]+var.ROLES["karga"]+var.ROLES["hain"]:
            if not del_player(cli, x, True):
                return
    
    var.FIRST_NIGHT = False

    td = var.DAY_START_TIME - var.NIGHT_START_TIME
    var.NIGHT_START_TIME = None
    var.NIGHT_TIMEDELTA += td
    min, sec = td.seconds // 60, td.seconds % 60

    found = {}
    for v in var.KILLS.values():
        if v in found:
            found[v] += 1
        else:
            found[v] = 1
    
    maxc = 0
    victim = ""
    dups = []
    for v, c in found.items():
        if c > maxc:
            maxc = c
            victim = v
            dups = []
        elif c == maxc:
            dups.append(v)

    if maxc:
        if dups:
            dups.append(victim)
            victim = random.choice(dups)
    
    message = [("gece bitti \u0002{0:0>2}:{1:0>2}\u0002. artik gunduz. "+
               "butun koyluler uyanik ve olmedikleri icin dualar ediyorlar, "+
               "koydeki kurtadami aramaya devam edecekler... ").format(min, sec)]
    dead = []
    crowonly = var.ROLES["karga"] and not var.ROLES["kurt"]
    if victim:
        var.LOGGER.logBare(victim, "WOLVESVICTIM", *[y for x,y in var.KILLS.items() if x == victim])
    for crow, target in iter(var.OBSERVED.items()):
        if ((target in list(var.HVISITED.keys()) and var.HVISITED[target]) or  # if var.HVISITED[target] is None, harlot visited self
            target in var.SEEN or (target in list(var.GUARDED.keys()) and var.GUARDED[target])):
            pm(cli, crow, ("Gun dogarken gordun ki \u0002{0}\u0002 butun gece boyunca "+
                          "yatagindaydi, ve evine geri uctun.").format(target))
        else:
            pm(cli, crow, ("Gun dogarken gordun ki \u0002{0}\u0002 butun gece uyudu "+
                          "ve evine geri uctun.").format(target))
    if victim in var.GUARDED.values():
        for gangel in var.ROLES["koruyucu melek"]:
            if var.GUARDED.get(gangel) == victim:
                dead.append(gangel)
                message.append(("\u0002{0}\u0002 hayatini bir baskasini kurtarmk icin "+
                        "feda etti.").format(gangel))
                break
        victim = ""
    elif not victim:
        message.append(random.choice(var.NO_VICTIMS_MESSAGES) +
                    " neyse ki tum koyluler bir sekilde hayatta kaldilar.")
    elif victim in var.ROLES["harlot"]:  # Attacked harlot, yay no kill
        if var.HVISITED.get(victim):
            message.append("kurtlar kendilerine butun gece evde olmayan "+
                           "fahiseyi sectiler.")
            victim = ""
    if victim and (victim not in var.ROLES["fahise"] or   # not a harlot
                          not var.HVISITED.get(victim)):   # harlot stayed home
        message.append(("\u0002{0}\u0002'in cesedi bir "+
                        "\u0002{1}\u0002 olarak bulundu. geride kalanlar bu trajedinin "+
                        "yasini tuttular :(").format(victim, var.get_role(victim)))
        dead.append(victim)
        var.LOGGER.logBare(victim, "KILLED")
        if random.random() < 1/50:
            message.append(random.choice(
                ["https://i.imgur.com/nO8rZ.gif",
                "https://i.imgur.com/uGVfZ.gif",
                "https://i.imgur.com/mUcM09n.gif",
                "https://i.imgur.com/P7TEGyQ.gif",
                "https://i.imgur.com/b8HAvjL.gif",
                "https://i.imgur.com/PIIfL15.gif"]
                ))
            
    if victim in var.GUNNERS.keys() and var.GUNNERS[victim]:  # victim had bullets!
        if random.random() < var.GUNNER_KILLS_WOLF_AT_NIGHT_CHANCE:
            wc = var.ROLES["karga"][:]
            for crow in wc:
                if crow in var.OBSERVED.keys():
                    wc.remove(crow)
            # don't kill off werecrows that observed
            deadwolf = random.choice(var.ROLES["kurt"]+wc)
            message.append(("neyse ki, kurban \02{0}\02, bir kac kursunu vardi ve "+
                            "\02{1}\02, bir \02{2}\02, vurularak olduruldu.").format(victim, deadwolf, var.get_role(deadwolf)))
            var.LOGGER.logBare(deadwolf, "KILLEDBYGUNNER")
            dead.append(deadwolf)
    if victim in var.HVISITED.values():  #  victim was visited by some harlot
        for hlt in var.HVISITED.keys():
            if var.HVISITED[hlt] == victim:
                message.append(("\02{0}\02, bir \02fahise\02, buyuk bir hata yaparak "+
                                "dun gece kurbanin evini ziyaret etti ve "+
                                "artik o da oldu.").format(hlt))
                dead.append(hlt)
    for harlot in var.ROLES["fahise"]:
        if var.HVISITED.get(harlot) in var.ROLES["kurt"]+var.ROLES["karga"]:
            message.append(("\02{0}\02, bir \02fahise\02, buyuk bir hata yaparak "+
                            "bir kurtadamin evini ziyaret etti ve "+
                            "artik olu.").format(harlot))
            dead.append(harlot)
    for gangel in var.ROLES["koruyucu melek"]:
        if var.GUARDED.get(gangel) in var.ROLES["kurt"]+var.ROLES["karga"]:
            if victim == gangel:
                continue # already dead.
            r = random.random()
            if r < var.GUARDIAN_ANGEL_DIES_CHANCE:
                message.append(("\02{0}\02, bir \02koruyucu melek\02, "+
                                "kurtadamin evini ziyaret etmek gibi buyuk bir hata "+
                                "yapti ve artik o da olu.").format(gangel))
                var.LOGGER.logBare(gangel, "KILLEDWHENGUARDINGWOLF")
                dead.append(gangel)
    cli.msg(chan, "\n".join(message))
    for msg in message:
        var.LOGGER.logMessage(msg.replace("\02", ""))
    
    for deadperson in dead:  # kill each player, but don't end the game if one group outnumbers another
        del_player(cli, deadperson, end_game = False)
    if chk_win(cli):  # if after the last person is killed, one side wins, then actually end the game here
        return
    
    if (var.WOLF_STEALS_GUN and victim in dead and 
        victim in var.GUNNERS.keys() and var.GUNNERS[victim] > 0):
        # victim has bullets
        guntaker = random.choice(var.ROLES["kurt"] + var.ROLES["karga"] 
                                 + var.ROLES["hain"])  # random looter
        numbullets = var.GUNNERS[victim]
        var.WOLF_GUNNERS[guntaker] = 1  # transfer bullets a wolf
        mmsg = ("{0}'in esyalarini karistirirken, bir tufek ve " + 
                "icerisinde bir gumus kursun buldun! " + 
                "bunu sadece gunduz kullanabilirsin. " +
                "eger bir kurtadami vurursan kasten kacirmis olacaksin. " +
                "eger bir koylu vurursan yaralanabilirler.")
        mmsg = mmsg.format(victim)
        pm(cli, guntaker, mmsg)
        var.GUNNERS[victim] = 0  # just in case

            
    begin_day(cli)


def chk_nightdone(cli):
    if (len(var.SEEN) >= len(var.ROLES["gozcu"]) and  # Seers have seen.
        len(var.HVISITED.keys()) >= len(var.ROLES["fahise"]) and  # harlots have visited.
        len(var.GUARDED.keys()) >= len(var.ROLES["koruyucu melek"]) and  # guardians have guarded
        len(var.KILLS)+len(var.OBSERVED) >= len(var.ROLES["karga"]+var.ROLES["kurt"]) and
        var.PHASE == "gece"):
        
        # check if wolves are actually agreeing
        if len(set(var.KILLS.values())) > 1:
            return
        
        for x, t in var.TIMERS.items():
            t.cancel()
        
        var.TIMERS = {}
        if var.PHASE == "gece":  # Double check
            transition_day(cli)



@cmd("linc", "oy", "v")
def vote(cli, nick, chann_, rest):
    """Use this to vote for a candidate to be lynched"""
    chan = botconfig.CHANNEL
    
    rest = re.split(" +",rest)[0].strip().lower()

    if not rest:
        show_votes(cli, nick, chan, rest)
        return
    if var.PHASE in ("yok", "oyun"):
        cli.notice(nick, "Su an bir oyun oynanmiyor.")
        return
    elif nick not in var.list_players() or nick in var.DISCONNECTED.keys():
        cli.notice(nick, "su an oynamiyorsun.")
        return
    if var.PHASE != "gunduz":
        cli.notice(nick, ("sadece gunduz birisini linc edebilirsin. "+
                          "sabirli ol ve sabahi bekle."))
        return
    if nick in var.WOUNDED:
        cli.msg(chan, ("{0}: yaralisin ve dinleniyorsun, "+
                      "bu gunluk oylama yapamazsin.").format(nick))
        return

    pl = var.list_players()
    pl_l = [x.strip().lower() for x in pl]
    
    matches = 0
    for player in pl_l:
        if rest == player:
            target = player
            break
        if player.startswith(rest):
            target = player
            matches += 1
    else:
        if matches != 1:
            pm(cli, nick, "\u0002{0}\u0002 su an oynamiyor.".format(rest))
            return
        
    voted = pl[pl_l.index(target)]

    if not var.SELF_LYNCH_ALLOWED:
        if nick == voted:
            cli.notice(nick, "kendini korumaya calismalisin.")
            return

    lcandidates = list(var.VOTES.keys())
    for voters in lcandidates:  # remove previous vote
        if nick in var.VOTES[voters]:
            var.VOTES[voters].remove(nick)
            if not var.VOTES.get(voters) and voters != voted:
                del var.VOTES[voters]
            break
    if voted not in var.VOTES.keys():
        var.VOTES[voted] = [nick]
    else:
        var.VOTES[voted].append(nick)
    cli.msg(chan, ("\u0002{0}\u0002, \u0002{1}\u0002 icin "+
                   "oy verdi.").format(nick, voted))
    var.LOGGER.logMessage("{0} votes for {1}.".format(nick, voted))
    var.LOGGER.logBare(voted, "VOTED", nick)
    
    var.LAST_VOTES = None # reset
    
    chk_decision(cli)



@cmd("vazgec")
def retract(cli, nick, chann_, rest):
    """Takes back your vote during the day (for whom to lynch)"""
    
    chan = botconfig.CHANNEL
    
    if var.PHASE in ("yok", "oyun"):
        cli.notice(nick, "su an bir oyun oynanmiyor.")
        return
    elif nick not in var.list_players() or nick in var.DISCONNECTED.keys():
        cli.notice(nick, "su an oynamiyorsun.")
        return
        
    if var.PHASE != "gunduz":
        cli.notice(nick, ("sadece gunduz birisini linc edebilirsin. "+
                          "sabirli ol ve sabahi bekle."))
        return

    candidates = var.VOTES.keys()
    for voter in list(candidates):
        if nick in var.VOTES[voter]:
            var.VOTES[voter].remove(nick)
            if not var.VOTES[voter]:
                del var.VOTES[voter]
            cli.msg(chan, "\u0002{0}\u0002 verdigi oydan vazgecti, baska biri suphe cekmis olmali.".format(nick))
            var.LOGGER.logBare(voter, "RETRACT", nick)
            var.LOGGER.logMessage("{0}'s vote was retracted.".format(nick))
            var.LAST_VOTES = None # reset
            break
    else:
        cli.notice(nick, "zaten oy kullanmamistin.")

@pmcmd("vagzec")
def wolfretract(cli, nick, rest):
    if var.PHASE in ("yok", "oyun"):
        cli.notice(nick, "su an oyun oynanmiyor.")
        return
    elif nick not in var.list_players() or nick in var.DISCONNECTED.keys():
        cli.notice(nick, "su an oynamiyorsun.")
        return
	
    role = var.get_role(nick)
    if role not in ('kurt', 'karga'):
        return
    if var.PHASE != "gece":
        pm(cli, nick, "sadece geceleri vazgecebilirsin.")
        return
    if role == "karga":  # Check if already observed
        if var.OBSERVED.get(nick):
            pm(cli, nick, ("zaten bir kargaya donustun, ve "+
                           "gun dogana kadar geri donemezsin."))
            return
	
    if nick in var.KILLS.keys():
        del var.KILLS[nick]
    pm(cli, nick, "verdigin oydan vazgectin.")
    #var.LOGGER.logBare(nick, "RETRACT", nick)

@cmd("vur")
def shoot(cli, nick, chann_, rest):
    """Use this to fire off a bullet at someone in the day if you have bullets"""
    
    chan = botconfig.CHANNEL
    if var.PHASE in ("yok", "oyun"):
        cli.notice(nick, "su an oyun oynanmiyor.")
        return
    elif nick not in var.list_players() or nick in var.DISCONNECTED.keys():
        cli.notice(nick, "su an oynamiyorsun.")
        return
        
    if var.PHASE != "gunduz":
        cli.notice(nick, ("sadece gunduzleri birini vurabilirsin. "+
                          "sabirli ol ve sabahi bekle."))
        return
    if not (nick in var.GUNNERS.keys() or nick in var.WOLF_GUNNERS.keys()):
        pm(cli, nick, "silahin yok.")
        return
    elif ((nick in var.GUNNERS.keys() and not var.GUNNERS[nick]) or
          (nick in var.WOLF_GUNNERS.keys() and not var.WOLF_GUNNERS[nick])):
        pm(cli, nick, "baska mermin kalmadi.")
        return
    victim = re.split(" +",rest)[0].strip().lower()
    if not victim:
        cli.notice(nick, "eksik parametre")
        return
    pl = var.list_players()
    pll = [x.lower() for x in pl]
    matches = 0
    for player in pll:
        if victim == player:
            target = player
            break
        if player.startswith(victim):
            target = player
            matches += 1
    else:
        if matches != 1:
            pm(cli, nick, "\u0002{0}\u0002 su an oynamiyor.".format(victim))
            return
    victim = pl[pll.index(target)]
    if victim == nick:
        cli.notice(nick, "yanlis yone tutuyorsun.")
        return
    
    wolfshooter = nick in var.ROLES["kurt"]+var.ROLES["karga"]+var.ROLES["hain"]
    
    if wolfshooter and nick in var.WOLF_GUNNERS:
        var.WOLF_GUNNERS[nick] -= 1
    else:
        var.GUNNERS[nick] -= 1
    
    rand = random.random()
    if nick in var.ROLES["sarhos koylu"]:
        chances = var.DRUNK_GUN_CHANCES
    else:
        chances = var.GUN_CHANCES
    
    wolfvictim = victim in var.ROLES["kurt"]+var.ROLES["karga"]
    if rand <= chances[0] and not (wolfshooter and wolfvictim):  # didn't miss or suicide
        # and it's not a wolf shooting another wolf
        
        cli.msg(chan, ("\u0002{0}\u0002, \u0002{1}\u0002'i gumus bir "+
                       "mermiyle vurdu!").format(nick, victim))
        var.LOGGER.logMessage("{0} shoots {1} with a silver bullet!".format(nick, victim))
        victimrole = var.get_reveal_role(victim)
        if victimrole in ("kurt", "karga"):
            cli.msg(chan, ("\u0002{0}\u0002 bir {1} idi, ve "+
                           "gumus mermi onu olduruyor.").format(victim, victimrole))
            var.LOGGER.logMessage(("{0} is a {1}, and is dying from the "+
                            "silver bullet.").format(victim, victimrole))
            if not del_player(cli, victim):
                return
        elif random.random() <= var.MANSLAUGHTER_CHANCE:
            cli.msg(chan, ("\u0002{0}\u0002 kurt degildi "+
                           "ama yanlislikla oldurucu sekilde yaralandi.").format(victim))
            cli.msg(chan, "koy bir \u0002{0}\u0002 kurban etti.".format(victimrole))
            var.LOGGER.logMessage("{0} is not a wolf but was accidentally fatally injured.".format(victim))
            var.LOGGER.logMessage("The village has sacrificed a {0}.".format(victimrole))
            if not del_player(cli, victim):
                return
        else:
            cli.msg(chan, ("\u0002{0}\u0002 bir koyluydu ve yaralandi. neyse ki "+
                          "bir gun dinlendikten sonra yasamaya devam "+
                          "edebilecek.").format(victim))
            var.LOGGER.logMessage(("{0} is a villager and was injured. Luckily "+
                          "the injury is minor and will heal after a day of "+
                          "rest.").format(victim))
            if victim not in var.WOUNDED:
                var.WOUNDED.append(victim)
            lcandidates = list(var.VOTES.keys())
            for cand in lcandidates:  # remove previous vote
                if victim in var.VOTES[cand]:
                    var.VOTES[cand].remove(victim)
                    if not var.VOTES.get(cand):
                        del var.VOTES[cand]
                    break
            chk_decision(cli)
            chk_win(cli)
    elif rand <= chances[0] + chances[1]:
        cli.msg(chan, "\u0002{0}\u0002 kotu bir avci ve iskaladi!".format(nick))
        var.LOGGER.logMessage("{0} is a lousy shooter and missed!".format(nick))
    else:
        cli.msg(chan, ("Hass...! \u0002{0}\u0002 kotu bir silaha sahipmis ve patladi! "+
                       "koyluler bir avci-\u0002{1}\u0002 icin yas tutuyor.").format(nick, var.get_reveal_role(nick)))
        var.LOGGER.logMessage(("Oh no! {0}'s gun was poorly maintained and has exploded! "+
                       "The village mourns a gunner-{1}.").format(nick, var.get_reveal_role(nick)))
        if not del_player(cli, nick):
            return  # Someone won.



@pmcmd("oldur")
def kill(cli, nick, rest):
    if var.PHASE in ("yok", "oyun"):
        cli.notice(nick, "su an oyun oynanmiyor.")
        return
    elif nick not in var.list_players() or nick in var.DISCONNECTED.keys():
        cli.notice(nick, "su an oynamiyorsun.")
        return
    role = var.get_role(nick)
    if role == "hain":
        return  # they do this a lot.
    if role not in ('kurt', 'karga'):
        pm(cli, nick, "bunu sadece bir kurt yapabilir.")
        return
    if var.PHASE != "gece":
        pm(cli, nick, "insanlari sadece gece oldurebilirsin.")
        return
    victim = re.split(" +",rest)[0].strip().lower()
    if not victim:
        pm(cli, nick, "eksik parametre")
        return
    if role == "karga":  # Check if flying to observe
        if var.OBSERVED.get(nick):
            pm(cli, nick, ("bir kargaya donustun, bu yuzden, "+
                           "fiziksel olarak bir koyluyu olduremezsin."))
            return
    pl = var.list_players()
    pll = [x.lower() for x in pl]
    
    matches = 0
    for player in pll:
        if victim == player:
            target = player
            break
        if player.startswith(victim):
            target = player
            matches += 1
    else:
        if matches != 1:
            pm(cli, nick, "\u0002{0}\u0002 su an oynamiyor.".format(victim))
            return
    
    victim = pl[pll.index(target)]
    if victim == nick:
        pm(cli, nick, "intihar kotudur, yapma.")
        return
    if victim in var.ROLES["kurt"]+var.ROLES["karga"]+var.ROLES["hain"]:
        pm(cli, nick, "sadece koyluleri oldurebilirsin, kurtadamlari degil.")
        return
    var.KILLS[nick] = victim
    pm(cli, nick, "\u0002{0}\u0002'i oldurulmesi icin kendine kurban sectin.".format(victim))
    var.LOGGER.logBare(nick, "SELECT", victim)
    chk_nightdone(cli)


@pmcmd("koru", "kurtar", "save")
def guard(cli, nick, rest):
    if var.PHASE in ("yok", "oyun"):
        cli.notice(nick, "su an oyun oynanmiyor.")
        return
    elif nick not in var.list_players() or nick in var.DISCONNECTED.keys():
        cli.notice(nick, "su an oynamiyorsun.")
        return
    role = var.get_role(nick)
    if role != 'koruyucu melek':
        pm(cli, nick, "sadece koruyucu melek birilerini koruyabilir.")
        return
    if var.PHASE != "gece":
        pm(cli, nick, "sadece geceleri birilerini koruyabilirsin.")
        return
    victim = re.split(" +",rest)[0].strip().lower()
    if not victim:
        pm(cli, nick, "eksik parametre")
        return
    if var.GUARDED.get(nick):
        pm(cli, nick, ("zaten \u0002{0}\u0002'i koruyorsun"+
                      ".").format(var.GUARDED[nick]))
        return
    pl = var.list_players()
    pll = [x.lower() for x in pl]
    matches = 0
    for player in pll:
        if victim == player:
            target = player
            break
        if player.startswith(victim):
            target = player
            matches += 1
    else:
        if matches != 1:
            pm(cli, nick, "\u0002{0}\u0002 su an oynamiyor.".format(victim))
            return
    victim = pl[pll.index(target)]
    if victim == nick:
        var.GUARDED[nick] = None
        pm(cli, nick, "bu gece korumak icin kimseyi secmedin.")
    else:
        var.GUARDED[nick] = victim
        pm(cli, nick, "bu gece \u0002{0}\u0002 senin koruman altinda. elveda!".format(var.GUARDED[nick]))
        pm(cli, var.GUARDED[nick], "bu gece rahat uyu, kutsandin ve bir koruyucu melek seni koruyor.")
        var.LOGGER.logBare(var.GUARDED[nick], "GUARDED", nick)
    chk_nightdone(cli)



@pmcmd("gozetle")
def observe(cli, nick, rest):
    if var.PHASE in ("yok", "oyun"):
        cli.notice(nick, "su an oyun oynanmiyor.")
        return
    elif nick not in var.list_players() or nick in var.DISCONNECTED.keys():
        cli.notice(nick, "su an oynamiyorsun.")
        return
    if not var.is_role(nick, "karga"):
        pm(cli, nick, "sadece bir karga birilerini gozetleyebilir.")
        return
    if var.PHASE != "gece":
        pm(cli, nick, "sadece geceleri bir kargaya donusebilirsin.")
        return
    victim = re.split(" +", rest)[0].strip().lower()
    if not victim:
        pm(cli, nick, "eksik parametre")
        return
    pl = var.list_players()
    pll = [x.lower() for x in pl]
    matches = 0
    for player in pll:
        if victim == player:
            target = player
            break
        if player.startswith(victim):
            target = player
            matches += 1
    else:
        if matches != 1:
            pm(cli, nick,"\u0002{0}\u0002 su an oynamiyor.".format(victim))
            return
    victim = pl[pll.index(target)]
    if victim == nick.lower():
        pm(cli, nick, "bunun yerine birini oldurmek isteyebilirsin.")
        return
    if nick in var.OBSERVED.keys():
        pm(cli, nick, "zaten \02{0}\02'in evine ucuyorsun. GAAK.".format(var.OBSERVED[nick]))
        return
    if var.get_role(victim) in ("karga", "hain", "kurt"):
        pm(cli, nick, "baska bir kurdun evini ziyaret etmek mantikli olmayabilir")
        return
    var.OBSERVED[nick] = victim
    if nick in var.KILLS.keys():
        del var.KILLS[nick]
    pm(cli, nick, ("buyuk bir kargaya donustun ve \u0002{0}'nin\u0002 evine "+
                   "ucmaya basladin. yeterince gozlemledikten sonra gun agarirken "+
                   "evine doneceksin.").format(victim))
    var.LOGGER.logBare(victim, "OBSERVED", nick)
    chk_nightdone(cli)


@pmcmd("kimlik")
def investigate(cli, nick, rest):
    if var.PHASE in ("yok", "oyun"):
        cli.notice(nick, "su an oyun oynanmiyor.")
        return
    elif nick not in var.list_players() or nick in var.DISCONNECTED.keys():
        cli.notice(nick, "su an oynamiyorsun.")
        return
    if not var.is_role(nick, "dedektif"):
        pm(cli, nick, "sadece bir dedektif birinin kimligini gorebilir.")
        return
    if var.PHASE != "gunduz":
        pm(cli, nick, "sadece gunduzleri birilerinin kimligini gorebilirsin.")
        return
    if nick in var.INVESTIGATED:
        pm(cli, nick, "her turda sadece bir kisinin kimligini ogrenebilirsin.")
        return
    victim = re.split(" +", rest)[0].strip().lower()
    if not victim:
        pm(cli, nick, "eksik parametre")
        return
    pl = var.list_players()
    pll = [x.lower() for x in pl]
    matches = 0
    for player in pll:
        if victim == player:
            target = player
            break
        if player.startswith(victim):
            target = player
            matches += 1
    else:
        if matches != 1:
            pm(cli, nick,"\u0002{0}\u0002 su an oynamiyor.".format(victim))
            return
    victim = pl[pll.index(target)]
    if victim == nick:
        pm(cli, nick, "kendi kimligine bakmak cok mantiksiz.")
        return

    var.INVESTIGATED.append(nick)
    pm(cli, nick, ("arastirmalarinin sonuclari sonunda ulasti. \u0002{0}\u0002"+
                   " bir... \u0002{1}\u0002!").format(victim, var.get_role(victim)))
    var.LOGGER.logBare(victim, "INVESTIGATED", nick)
    if random.random() < var.DETECTIVE_REVEALED_CHANCE:  # a 2/5 chance (should be changeable in settings)
        # The detective's identity is compromised!
        for badguy in var.ROLES["kurt"] + var.ROLES["karga"] + var.ROLES["hain"]:
            pm(cli, badguy, ("birisi cebinden bir kagit dusurdu. gorunen o ki "+
                            "\u0002{0}\u0002 dedektifmis!").format(nick))        
        var.LOGGER.logBare(nick, "PAPERDROP")



@pmcmd("ziyaret")
def hvisit(cli, nick, rest):
    if var.PHASE in ("yok", "oyun"):
        cli.notice(nick, "su an oyun oynanmiyor")
        return
    elif nick not in var.list_players() or nick in var.DISCONNECTED.keys():
        cli.notice(nick, "su an oynamiyorsun.")
        return
    if not var.is_role(nick, "fahise"):
        pm(cli, nick, "sadece bir fahise birinin evini ziyaret edebilir.")
        return
    if var.PHASE != "gece":
        pm(cli, nick, "birini sadece gece ziyaret edebilirsin.")
        return
    if var.HVISITED.get(nick):
        pm(cli, nick, ("zaten butun geceyi "+
                      "\u0002{0}\u0002 ile geciriyorsun.").format(var.HVISITED[nick]))
        return
    victim = re.split(" +",rest)[0].strip().lower()
    if not victim:
        pm(cli, nick, "eksik parametre")
        return
    pll = [x.lower() for x in var.list_players()]
    matches = 0
    for player in pll:
        if victim == player:
            target = player
            break
        if player.startswith(victim):
            target = player
            matches += 1
    else:
        if matches != 1:
            pm(cli, nick,"\u0002{0}\u0002 su an oynamiyor.".format(victim))
            return
    victim = var.list_players()[pll.index(target)]
    if nick == victim:  # Staying home
        var.HVISITED[nick] = None
        pm(cli, nick, "bu gece evde kalmayi tercih ettin.")
    else:
        var.HVISITED[nick] = victim
        pm(cli, nick, ("bu geceyi \u0002{0}\u0002 ile geciriyorsun. "+
                      "iyi geceler!").format(var.HVISITED[nick]))
        pm(cli, var.HVISITED[nick], ("bu geceyi \u0002{0}"+
                                     "\u0002 ile geciriyorsun. iyi geceler!").format(nick))
        var.LOGGER.logBare(var.HVISITED[nick], "VISITED", nick)
    chk_nightdone(cli)


def is_fake_nick(who):
    return not(re.search("^[a-zA-Z\\\_\]\[`]([a-zA-Z0-9\\\_\]\[`]+)?", who)) or who.lower().endswith("serv")



@pmcmd("gor")
def see(cli, nick, rest):
    if var.PHASE in ("yok", "oyun"):
        cli.notice(nick, "su an oyun oynanmiyor.")
        return
    elif nick not in var.list_players() or nick in var.DISCONNECTED.keys():
        cli.notice(nick, "su an oynamiyorsun.")
        return
    if not var.is_role(nick, "gozcu"):
        pm(cli, nick, "sadece gozculer birini gorebilir.")
        return
    if var.PHASE != "gece":
        pm(cli, nick, "sadece geceleri birilrini gorebilirsin.")
        return
    if nick in var.SEEN:
        pm(cli, nick, "sadece her turda bir kere birilerini gorebilirsin.")
        return
    victim = re.split(" +",rest)[0].strip().lower()
    pl = var.list_players()
    pll = [x.lower() for x in pl]
    if not victim:
        pm(cli, nick, "eksik parametre")
        return
    matches = 0
    for player in pll:
        if victim == player:
            target = player
            break
        if player.startswith(victim):
            target = player
            matches += 1
    else:
        if matches != 1:
            pm(cli, nick,"\u0002{0}\u0002 su an oynamiyor.".format(victim))
            return
    victim = pl[pll.index(target)]
    if victim == nick:
        pm(cli, nick, "kendini gormeye calismak cok mantiksiz.")
        return
    if victim in var.CURSED or var.get_role(victim) == "karga":
        role = "kurt"
    elif var.get_role(victim) == "hain":
        role = "koylu"
    else:
        role = var.get_role(victim)
    pm(cli, nick, ("bir seyleri gorebilme kabiliyetin var ve, "+
                    "gordugun kadariyla \u0002{0}\u0002 bir "+
                    "\u0002{1}\u0002!").format(victim, role))
    var.SEEN.append(nick)
    var.LOGGER.logBare(victim, "SEEN", nick)
    chk_nightdone(cli)



@hook("featurelist")  # For multiple targets with PRIVMSG
def getfeatures(cli, nick, *rest):
    for r in rest:
        if r.startswith("TARGMAX="):
            x = r[r.index("PRIVMSG:"):]
            if "," in x:
                l = x[x.index(":")+1:x.index(",")]
            else:
                l = x[x.index(":")+1:]
            l = l.strip()
            if not l or not l.isdigit():
                continue
            else:
                var.MAX_PRIVMSG_TARGETS = int(l)
                break



def mass_privmsg(cli, targets, msg, notice = False):
    while targets:
        if len(targets) <= var.MAX_PRIVMSG_TARGETS:
            bgs = ",".join(targets)
            targets = ()
        else:
            bgs = ",".join(targets[0:var.MAX_PRIVMSG_TARGETS])
            targets = targets[var.MAX_PRIVMSG_TARGETS:]
        if not notice:
            cli.msg(bgs, msg)
        else:
            cli.notice(bgs, msg)
                
                

@pmcmd("")
def relay(cli, nick, rest):
    """Let the wolves talk to each other through the bot"""
    if var.PHASE not in ("gece", "gunduz"):
        return

    badguys = var.ROLES["kurt"] + var.ROLES["hain"] + var.ROLES["karga"]
    if len(badguys) > 1:
        if nick in badguys:
            badguys.remove(nick)  #  remove self from list
        
            if rest.startswith("\01ACTION"):
                rest = rest[7:-1]
                mass_privmsg(cli, [guy for guy in badguys 
                    if (guy in var.PLAYERS and
                        var.PLAYERS[guy]["cloak"] not in var.SIMPLE_NOTIFY)], "\02{0}\02{1}".format(nick, rest))
                mass_privmsg(cli, [guy for guy in badguys 
                    if (guy in var.PLAYERS and
                        var.PLAYERS[guy]["cloak"] in var.SIMPLE_NOTIFY)], nick+rest, True)
            else:
                mass_privmsg(cli, [guy for guy in badguys 
                    if (guy in var.PLAYERS and
                        var.PLAYERS[guy]["cloak"] not in var.SIMPLE_NOTIFY)], "\02{0}\02 diyor ki: {1}".format(nick, rest))
                mass_privmsg(cli, [guy for guy in badguys 
                    if (guy in var.PLAYERS and
                        var.PLAYERS[guy]["cloak"] in var.SIMPLE_NOTIFY)], "\02{0}\02 diyor ki: {1}".format(nick, rest), True)



def transition_night(cli):
    if var.PHASE == "gece":
        return
    var.PHASE = "gece"

    for x, tmr in var.TIMERS.items():  # cancel daytime timer
        tmr.cancel()
    var.TIMERS = {}

    # Reset nighttime variables
    var.KILLS = {}
    var.GUARDED = {}  # key = by whom, value = the person that is visited
    var.KILLER = ""  # nickname of who chose the victim
    var.SEEN = []  # list of seers that have had visions
    var.OBSERVED = {}  # those whom werecrows have observed
    var.HVISITED = {}
    var.NIGHT_START_TIME = datetime.now()

    daydur_msg = ""

    if var.NIGHT_TIMEDELTA or var.START_WITH_DAY:  #  transition from day
        td = var.NIGHT_START_TIME - var.DAY_START_TIME
        var.DAY_START_TIME = None
        var.DAY_TIMEDELTA += td
        min, sec = td.seconds // 60, td.seconds % 60
        daydur_msg = "Gunduz bitti \u0002{0:0>2}:{1:0>2}\u0002. ".format(min,sec)

    chan = botconfig.CHANNEL

    if var.NIGHT_TIME_LIMIT > 0:
        var.NIGHT_ID = time.time()
        t = threading.Timer(var.NIGHT_TIME_LIMIT, transition_day, [cli, var.NIGHT_ID])
        var.TIMERS["gece"] = t
        var.TIMERS["gece"].daemon = True
        t.start()
        
    if var.NIGHT_TIME_WARN > 0:
        t2 = threading.Timer(var.NIGHT_TIME_WARN, night_warn, [cli, var.NIGHT_ID])
        var.TIMERS["gece_uyarisi"] = t2
        var.TIMERS["gece_uyarisi"].daemon = True
        t2.start()

    # send PMs
    ps = var.list_players()
    wolves = var.ROLES["kurt"]+var.ROLES["hain"]+var.ROLES["karga"]
    for wolf in wolves:
        normal_notify = wolf in var.PLAYERS and var.PLAYERS[wolf]["cloak"] not in var.SIMPLE_NOTIFY
    
        if normal_notify:
            if wolf in var.ROLES["kurt"]:
                pm(cli, wolf, ('sen bir \u0002kurtadamsın\u0002. butun koyluleri oldurmek '+
                               'senin gorevin. oldurmek icin "oldur <nick>" yaz.'))
            elif wolf in var.ROLES["hain"]:
                pm(cli, wolf, (('sen bir \u0002{0}\u0002\'sin. bir koylu gibi gorunursun. '+
                               'gozcu bile senin tam olarak kim oldugunu bilemez. '+
                               'ama dedektifler bilebilir. ').format(
                               "lanetli koylu" if wolf in var.CURSED else "hain")))
            else:
                pm(cli, wolf, ('sen bir \u0002kargasin\u0002. geceleri ucabilirsin. '+
                               '"oldur <nick>" yazarak bir koyluyu oldurmeye yardim edebilirsin.'+
                               'ayrica "gozetle <nick>" yazarak birinin uyuyup uyumadigina bakabilirsin. '+
                               'gozetlemek seni bir olume karismaktan kurtarabilir.'))
            if len(wolves) > 1:
                pm(cli, wolf, 'eger bana bir sey yazarsan, yazdiklarin diger kurtlara iletilir.')
        else:
            role = var.get_role(wolf)
            pm(cli, wolf, "sen bir \02{0}\02'sin.".format("lanetli koylu" if role == "hain" and wolf in var.CURSED else role))  # !simple
            
        
        pl = ps[:]
        random.shuffle(pl)
        pl.remove(wolf)  # remove self from list
        for i, player in enumerate(pl):
            if player in var.ROLES["kurt"]:
                pl[i] = "" + player + " (kurt)"
            elif player in var.ROLES["hain"]:
                if player in var.CURSED:
                    pl[i] = "" + player + " (lanetli hain)"
                else:
                    pl[i] = "" + player + " (hain)"
            elif player in var.ROLES["karga"]:
                pl[i] = "" + player + " (karga)"
            elif player in var.CURSED:
                pl[i] = player + " (lanetli)"

        pm(cli, wolf, "oyuncular: "+", ".join(pl))

    for seer in var.ROLES["gozcu"]:
        pl = ps[:]
        random.shuffle(pl)
        pl.remove(seer)  # remove self from list
        
        if seer in var.PLAYERS and var.PLAYERS[seer]["cloak"] not in var.SIMPLE_NOTIFY:
            pm(cli, seer, ('sen bir \u0002gozcusun\u0002. '+
                          'her gece bir kurtadami gorebilme yetenegin var '+
                          '"gor <nick>" yaz ve onun kim oldugunu gor.'))
        else:
            pm(cli, seer, "sen bir \02gozcusun\02.")  # !simple
        pm(cli, seer, "oyuncular: "+", ".join(pl))

    for harlot in var.ROLES["fahise"]:
        pl = ps[:]
        random.shuffle(pl)
        pl.remove(harlot)
        if harlot in var.PLAYERS and var.PLAYERS[harlot]["cloak"] not in var.SIMPLE_NOTIFY:
            cli.msg(harlot, ('sen bir \u0002fahisesin\u0002. '+
                             'bir geceyi birileriyle gecirebilirsin. '+
                             'eger bir kurtadami veya kurbani ziyaret edersen, '+
                             'olursun. "ziyaret <nick>" yazarak birini ziyaret edebilirsin.'))
        else:
            cli.notice(harlot, "sen bir \02fahisesin\02.")  # !simple
        pm(cli, harlot, "oyuncular: "+", ".join(pl))

    for g_angel in var.ROLES["koruyucu melek"]:
        pl = ps[:]
        random.shuffle(pl)
        pl.remove(g_angel)
        if g_angel in var.PLAYERS and var.PLAYERS[g_angel]["cloak"] not in var.SIMPLE_NOTIFY:
            cli.msg(g_angel, ('sen bir \u0002koruyucu meleksin\u0002. '+
                              'It is your job to protect the villagers. If you guard a'+
                              ' wolf, there is a 50/50 chance of you dying, if you guard '+
                              'a victim, they will live. Use guard to guard a player.'))
        else:
            cli.notice(g_angel, "You are a \02guardian angel\02.")  # !simple
        pm(cli, g_angel, "Players: " + ", ".join(pl))
    
    for dttv in var.ROLES["dedektif"]:
        pl = ps[:]
        random.shuffle(pl)
        pl.remove(dttv)
        if dttv in var.PLAYERS and var.PLAYERS[dttv]["cloak"] not in var.SIMPLE_NOTIFY:
            cli.msg(dttv, ("You are a \u0002detective\u0002.\n"+
                          "It is your job to determine all the wolves and traitors. "+
                          "Your job is during the day, and you can see the true "+
                          "identity of all users, even traitors.\n"+
                          "But, each time you use your ability, you risk a 2/5 "+
                          "chance of having your identity revealed to the wolves. So be "+
                          "careful. Use \"{0}id\" to identify any player during the day.").format(botconfig.CMD_CHAR))
        else:
            cli.notice(dttv, "You are a \02detective\02.")  # !simple
        pm(cli, dttv, "Players: " + ", ".join(pl))
    for drunk in var.ROLES["sarhos koylu"]:
        if drunk in var.PLAYERS and var.PLAYERS[drunk]["cloak"] not in var.SIMPLE_NOTIFY:
            cli.msg(drunk, "You have been drinking too much! You are the \u0002village drunk\u0002.")
        else:
            cli.notice(drunk, "You are the \u0002village drunk\u0002.")

    for g in tuple(var.GUNNERS.keys()):
        if g not in ps:
            continue
        elif not var.GUNNERS[g]:
            continue
        norm_notify = g in var.PLAYERS and var.PLAYERS[g]["cloak"] not in var.SIMPLE_NOTIFY
        if norm_notify:
            gun_msg =  ("You hold a gun that shoots special silver bullets. You may only use it "+
                        "during the day. Wolves and the crow will die instantly when shot, but "+
                        "a villager or traitor will likely survive. You get {0}.")
        else:
            gun_msg = ("You have a \02gun\02 with {0}.")
        if var.GUNNERS[g] == 1:
            gun_msg = gun_msg.format("1 mermi")
        elif var.GUNNERS[g] > 1:
            gun_msg = gun_msg.format(str(var.GUNNERS[g]) + " mermi")
        else:
            continue
        
        pm(cli, g, gun_msg)

    dmsg = (daydur_msg + "simdi gece oldu. butun herkes "+
                   "benden gelecek olan PM'i beklesin ve soylediklerimi takip etsin. "+
                   "eger hic mesaj almadiysaniz, "+
                   "rahatlayin ve sabah olmasini bekleyin.")
    cli.msg(chan, dmsg)
    var.LOGGER.logMessage(dmsg.replace("\02", ""))
    var.LOGGER.logBare("NIGHT", "BEGIN")

    # cli.msg(chan, "DEBUG: "+str(var.ROLES))
    if not var.ROLES["kurt"] + var.ROLES["karga"]:  # Probably something interesting going on.
        chk_nightdone(cli)
        chk_traitor(cli)



def cgamemode(cli, *args):
    chan = botconfig.CHANNEL
    if var.ORIGINAL_SETTINGS:  # needs reset
        reset_settings()
    
    for arg in args:
        modeargs = arg.split("=", 1)
        
        if len(modeargs) < 2:  # no equal sign in the middle of the arg
            cli.msg(botconfig.CHANNEL, "Invalid syntax.")
            return False
        
        modeargs[0] = modeargs[0].strip()
        if modeargs[0] in var.GAME_MODES.keys():
            md = modeargs.pop(0)
            modeargs[0] = modeargs[0].strip()
            try:
                gm = var.GAME_MODES[md](modeargs[0])
                for attr in dir(gm):
                    val = getattr(gm, attr)
                    if (hasattr(var, attr) and not callable(val)
                                            and not attr.startswith("_")):
                        var.ORIGINAL_SETTINGS[attr] = getattr(var, attr)
                        setattr(var, attr, val)
                return True
            except var.InvalidModeException as e:
                cli.msg(botconfig.CHANNEL, "Invalid mode: "+str(e))
                return False
        else:
            cli.msg(chan, "Mode \u0002{0}\u0002 not found.".format(modeargs[0]))


@cmd("basla")
def start(cli, nick, chann_, rest):
    """Starts a game of Werewolf"""
    
    chan = botconfig.CHANNEL
    
    villagers = var.list_players()
    pl = villagers[:]

    if var.PHASE == "yok":
        cli.notice(nick, "No game is currently running.")
        return
    if var.PHASE != "oyun":
        cli.notice(nick, "Werewolf is already in play.")
        return
    if nick not in villagers and nick != chan:
        cli.notice(nick, "You're currently not playing.")
        return
        
    now = datetime.now()
    var.GAME_START_TIME = now  # Only used for the idler checker
    dur = int((var.CAN_START_TIME - now).total_seconds())
    if dur > 0:
        cli.msg(chan, "lutfen en az {0} saniye daha bekleyin.".format(dur))
        return

    if len(villagers) < var.MIN_PLAYERS:
        cli.msg(chan, "{0}: oynamak icin \u0002{1}\u0002 veya daha fazla oyuncu gerekiyor.".format(nick, var.MIN_PLAYERS))
        return

    for pcount in range(len(villagers), var.MIN_PLAYERS - 1, -1):
        addroles = var.ROLES_GUIDE.get(pcount)
        if addroles:
            break
    else:
        cli.msg(chan, "{0}: No game settings are defined for \u0002{1}\u0002 player games.".format(nick, len(villagers)))
        return

    # Cancel join timer
    if 'oyun' in var.TIMERS:
        var.TIMERS['oyun'].cancel()
        del var.TIMERS['oyun']
        
    if var.ORIGINAL_SETTINGS:  # Custom settings
        while True:
            wvs = (addroles[var.INDEX_OF_ROLE["kurt"]] +
                  addroles[var.INDEX_OF_ROLE["hain"]])
            if len(villagers) < (sum(addroles) - addroles[var.INDEX_OF_ROLE["avci"]] -
                    addroles[var.INDEX_OF_ROLE["lanetli koylu"]]):
                cli.msg(chan, "There are too few players in the "+
                              "game to use the custom roles.")
            elif not wvs:
                cli.msg(chan, "There has to be at least one wolf!")
            elif wvs > (len(villagers) / 2):
                cli.msg(chan, "Too many wolves.")
            else:
                break
            reset_settings()
            cli.msg(chan, "The default settings have been restored.  Please !basla again.")
            var.PHASE = "oyun"
            return

            
    if var.ADMIN_TO_PING:
        if "oyna" in COMMANDS.keys():
            COMMANDS["oyna"] = [lambda *spam: cli.msg(chan, "This command has been disabled by an admin.")]
        if "basla" in COMMANDS.keys():
            COMMANDS["basla"] = [lambda *spam: cli.msg(chan, "This command has been disabled by an admin.")]

    var.ROLES = {}
    var.CURSED = []
    var.GUNNERS = {}
    var.WOLF_GUNNERS = {}
    var.SEEN = []
    var.OBSERVED = {}
    var.KILLS = {}
    var.GUARDED = {}
    var.HVISITED = {}

    villager_roles = ("avci", "lanetli koylu")
    for i, count in enumerate(addroles):
        role = var.ROLE_INDICES[i]
        if role in villager_roles:
            var.ROLES[role] = [None] * count
            continue # We deal with those later, see below
        selected = random.sample(villagers, count)
        var.ROLES[role] = selected
        for x in selected:
            villagers.remove(x)

    # Now for the villager roles
    # Select cursed (just a villager)
    if var.ROLES["lanetli koylu"]:
        possiblecursed = pl[:]
        for cannotbe in (var.ROLES["kurt"] + var.ROLES["karga"] +
                         var.ROLES["gozcu"]):
                                              # traitor can be cursed
            possiblecursed.remove(cannotbe)
        
        var.CURSED = random.sample(possiblecursed, len(var.ROLES["lanetli koylu"]))
    del var.ROLES["lanetli koylu"]
    
    # Select gunner (also a villager)
    if var.ROLES["avci"]:
                   
        possible = pl[:]
        for cannotbe in (var.ROLES["kurt"] + var.ROLES["karga"] +
                         var.ROLES["hain"]):
            possible.remove(cannotbe)
            
        for csd in var.CURSED:  # cursed cannot be gunner
            if csd in possible:
                possible.remove(csd)
                
        for gnr in random.sample(possible, len(var.ROLES["avci"])):
            if gnr in var.ROLES["sarhos koylu"]:
                var.GUNNERS[gnr] = (var.DRUNK_SHOTS_MULTIPLIER * 
                                    math.ceil(var.SHOTS_MULTIPLIER * len(pl)))
            else:
                var.GUNNERS[gnr] = math.ceil(var.SHOTS_MULTIPLIER * len(pl))
    del var.ROLES["avci"]

    var.SPECIAL_ROLES["keci cobani"] = []
    if var.GOAT_HERDER:
       var.SPECIAL_ROLES["keci cobani"] = [ nick ]

    var.ROLES["koylu"] = villagers

    cli.msg(chan, ("{0}: kurtadam'a hosgeldin, populer sosyal "+
                   "oyun.").format(", ".join(pl)))
    cli.mode(chan, "+m")

    var.ORIGINAL_ROLES = copy.deepcopy(var.ROLES)  # Make a copy
    
    var.DAY_TIMEDELTA = timedelta(0)
    var.NIGHT_TIMEDELTA = timedelta(0)
    var.DAY_START_TIME = datetime.now()
    var.NIGHT_START_TIME = datetime.now()

    var.LAST_PING = None
    
    var.LOGGER.log("Game Start")
    var.LOGGER.logBare("GAME", "BEGIN", nick)
    var.LOGGER.logBare(str(len(pl)), "PLAYERCOUNT")
    
    var.LOGGER.log("***")
    var.LOGGER.log("ROLES: ")
    for rol in var.ROLES:
        r = []
        for rw in var.plural(rol).split(" "):
            rwu = rw[0].upper()
            if len(rw) > 1:
                rwu += rw[1:]
            r.append(rwu)
        r = " ".join(r)
        var.LOGGER.log("{0}: {1}".format(r, ", ".join(var.ROLES[rol])))
        
        for plr in var.ROLES[rol]:
            var.LOGGER.logBare(plr, "ROLE", rol)
    
    if var.CURSED:
        var.LOGGER.log("Cursed Villagers: "+", ".join(var.CURSED))
        
        for plr in var.CURSED:
            var.LOGGER.logBare(plr+" ROLE cursed villager")
    if var.GUNNERS:
        var.LOGGER.log("Villagers With Bullets: "+", ".join([x+"("+str(y)+")" for x,y in var.GUNNERS.items()]))
        for plr in var.GUNNERS:
            var.LOGGER.logBare(plr, "ROLE gunner")
    
    var.LOGGER.log("***")        
        
    var.PLAYERS = {plr:dict(var.USERS[plr]) for plr in pl if plr in var.USERS}    

    if not var.START_WITH_DAY:
        var.FIRST_NIGHT = True
        transition_night(cli)
    else:
        transition_day(cli)

    for cloak in list(var.STASISED.keys()):
        var.STASISED[cloak] -= 1
        if var.STASISED[cloak] <= 0:
            del var.STASISED[cloak]

    # DEATH TO IDLERS!
    reapertimer = threading.Thread(None, reaper, args=(cli,var.GAME_ID))
    reapertimer.daemon = True
    reapertimer.start()

    
    
@hook("error")
def on_error(cli, pfx, msg):
    if msg.endswith("(Excess Flood)"):
        restart_program(cli, "excess flood", "")
    elif msg.startswith("Closing Link:"):
        raise SystemExit


@cmd("fstasis", admin_only=True)
def fstasis(cli, nick, chan, rest):
    """Admin command for removing or setting stasis penalties."""
    data = rest.split()
    msg = None
    if data:
        lusers = {k.lower(): v for k, v in var.USERS.items()}
        user = data[0].lower()
        
        if user not in lusers:
            pm(cli, nick, "Sorry, {0} cannot be found.".format(data[0]))
            return
        
        cloak = lusers[user]['cloak']

        if len(data) == 1:
            if cloak in var.STASISED:
                msg = "{0} ({1}) is in stasis for {2} games.".format(data[0], cloak, var.STASISED[cloak])
            else:
                msg = "{0} ({1}) is not in stasis.".format(data[0], cloak)
        else:
            try:
                amt = int(data[1])
            except ValueError:
                pm(cli, nick, "Sorry, invalid integer argument.")
                return
                
            if amt > 0:
                var.STASISED[cloak] = amt
                msg = "{0} ({1}) is now in stasis for {2} games.".format(data[0], cloak, amt)
            else:
                if cloak in var.STASISED:
                    del var.STASISED[cloak]
                    msg = "{0} ({1}) is no longer in stasis.".format(data[0], cloak)
                else:
                    msg = "{0} ({1}) is not in stasis.".format(data[0], cloak)
    else:
        if var.STASISED:
            msg = "Currently stasised: {0}".format(", ".join(
                "{0}: {1}".format(cloak, number)
                for cloak, number in var.STASISED.items()))
        else:
            msg = "Nobody is currently stasised."

    if msg:
        if chan == nick:
            pm(cli, nick, msg)
        else:
            cli.msg(chan, msg)

@pmcmd("fstasis", admin_only=True)
def fstasis_pm(cli, nick, rest):
    fstasis(cli, nick, nick, rest)



@cmd("bekle", "w")
def wait(cli, nick, chann_, rest):
    """Increase the wait time (before !start can be used)"""
    pl = var.list_players()
    
    chan = botconfig.CHANNEL
    
    
    if var.PHASE == "yok":
        cli.notice(nick, "No game is currently running.")
        return
    if var.PHASE != "oyun":
        cli.notice(nick, "Werewolf is already in play.")
        return
    if nick not in pl:
        cli.notice(nick, "You're currently not playing.")
        return
    if var.WAITED >= var.MAXIMUM_WAITED:
        cli.msg(chan, "Limit has already been reached for extending the wait time.")
        return

    now = datetime.now()
    if now > var.CAN_START_TIME:
        var.CAN_START_TIME = now + timedelta(seconds=var.EXTRA_WAIT)
    else:
        var.CAN_START_TIME += timedelta(seconds=var.EXTRA_WAIT)
    var.WAITED += 1
    cli.msg(chan, ("\u0002{0}\u0002 oyun baslangic zamanini "+
                  "{1} saniye uzatti.").format(nick, var.EXTRA_WAIT))



@cmd("fwait", admin_only=True)
def fwait(cli, nick, chann_, rest):

    pl = var.list_players()
    
    chan = botconfig.CHANNEL
    
    
    if var.PHASE == "yok":
        cli.notice(nick, "No game is currently running.")
        return
    if var.PHASE != "oyun":
        cli.notice(nick, "Werewolf is already in play.")
        return

    rest = re.split(" +", rest.strip(), 1)[0]
    if rest and (rest.isdigit() or (rest[0] == '-' and rest[1:].isdigit())):
        if len(rest) < 4:
            extra = int(rest)
        else:
            cli.msg(chan, "{0}: We don't have all day!".format(nick))
            return
    else:
        extra = var.EXTRA_WAIT
        
    now = datetime.now()
    if now > var.CAN_START_TIME:
        var.CAN_START_TIME = now + timedelta(seconds=extra)
    else:
        var.CAN_START_TIME += timedelta(seconds=extra)
    var.WAITED += 1
    cli.msg(chan, ("\u0002{0}\u0002 forcibly increased the wait time by "+
                  "{1} seconds.").format(nick, extra))


@cmd("fstop",admin_only=True)
def reset_game(cli, nick, chan, rest):
    if var.PHASE == "yok":
        cli.notice(nick, "No game is currently running.")
        return
    cli.msg(botconfig.CHANNEL, "\u0002{0}\u0002 has forced the game to stop.".format(nick))
    var.LOGGER.logMessage("{0} has forced the game to stop.".format(nick))
    if var.PHASE != "oyun":
        stop_game(cli)
    else:
        reset_modes_timers(cli)
        reset(cli)


@pmcmd("rules")
def pm_rules(cli, nick, rest):
    cli.notice(nick, var.RULES)

@cmd("rules")
def show_rules(cli, nick, chan, rest):
    """Displays the rules"""
    if var.PHASE in ("gunduz", "gece") and nick not in var.list_players():
        cli.notice(nick, var.RULES)
        return
    cli.msg(botconfig.CHANNEL, var.RULES)
    var.LOGGER.logMessage(var.RULES)


@pmcmd("help", raw_nick = True)
def get_help(cli, rnick, rest):
    """Gets help."""
    nick, mode, user, cloak = parse_nick(rnick)
    fns = []

    rest = rest.strip().replace(botconfig.CMD_CHAR, "", 1).lower()
    splitted = re.split(" +", rest, 1)
    cname = splitted.pop(0)
    rest = splitted[0] if splitted else ""
    found = False
    if cname:
        for c in (COMMANDS,PM_COMMANDS):
            if cname in c.keys():
                found = True
                for fn in c[cname]:
                    if fn.__doc__:
                        if callable(fn.__doc__):
                            pm(cli, nick, botconfig.CMD_CHAR+cname+": "+fn.__doc__(rest))
                            if nick == botconfig.CHANNEL:
                                var.LOGGER.logMessage(botconfig.CMD_CHAR+cname+": "+fn.__doc__(rest))
                        else:
                            pm(cli, nick, botconfig.CMD_CHAR+cname+": "+fn.__doc__)
                            if nick == botconfig.CHANNEL:
                                var.LOGGER.logMessage(botconfig.CMD_CHAR+cname+": "+fn.__doc__)
                        return
                    else:
                        continue
                else:
                    continue
        else:
            if not found:
                pm(cli, nick, "Command not found.")
            else:
                pm(cli, nick, "Documentation for this command is not available.")
            return
    # if command was not found, or if no command was given:
    for name, fn in COMMANDS.items():
        if (name and not fn[0].admin_only and 
            not fn[0].owner_only and name not in fn[0].aliases):
            fns.append("\u0002"+name+"\u0002")
    afns = []
    if is_admin(cloak) or cloak in botconfig.OWNERS: # todo - is_owner
        for name, fn in COMMANDS.items():
            if fn[0].admin_only and name not in fn[0].aliases:
                afns.append("\u0002"+name+"\u0002")
    cli.notice(nick, "Commands: "+", ".join(fns))
    if afns:
        cli.notice(nick, "Admin Commands: "+", ".join(afns))



@cmd("help", raw_nick = True)
def help2(cli, nick, chan, rest):
    """Gets help"""
    get_help(cli, nick, rest)


@hook("invite", raw_nick = False, admin_only = True)
def on_invite(cli, nick, something, chan):
    if chan == botconfig.CHANNEL:
        cli.join(chan)

      
def is_admin(cloak):
    return bool([ptn for ptn in botconfig.OWNERS+botconfig.ADMINS if fnmatch.fnmatch(cloak.lower(), ptn.lower())])


@cmd("admins", "ops")
def show_admins(cli, nick, chan, rest):
    """Pings the admins that are available."""
    admins = []
    pl = var.list_players()
    
    if (var.LAST_ADMINS and
        var.LAST_ADMINS + timedelta(seconds=var.ADMINS_RATE_LIMIT) > datetime.now()):
        cli.notice(nick, ("This command is rate-limited. " +
                          "Please wait a while before using it again."))
        return
        
    if not (var.PHASE in ("gunduz", "gece") and nick not in pl):
        var.LAST_ADMINS = datetime.now()
    
    if var.ADMIN_PINGING:
        return
    var.ADMIN_PINGING = True

    @hook("whoreply", hookid = 4)
    def on_whoreply(cli, server, dunno, chan, dunno1,
                    cloak, dunno3, user, status, dunno4):
        if not var.ADMIN_PINGING:
            return
        if (is_admin(cloak) and 'G' not in status and
            user != botconfig.NICK):
            admins.append(user)

    @hook("endofwho", hookid = 4)
    def show(*args):
        if not var.ADMIN_PINGING:
            return
        admins.sort(key=lambda x: x.lower())
        
        if chan == nick:
            pm(cli, nick, "Available admins: "+" ".join(admins))
        elif var.PHASE in ("gunduz", "gece") and nick not in pl:
            cli.notice(nick, "Available admins: "+" ".join(admins))
        else:
            cli.msg(chan, "Available admins: "+" ".join(admins))

        decorators.unhook(HOOKS, 4)
        var.ADMIN_PINGING = False

    cli.who(chan)

@pmcmd("admins", "ops")
def show_admins_pm(cli, nick, rest):
    show_admins(cli, nick, nick, rest)



@cmd("yazitura")
def coin(cli, nick, chan, rest):
    """It's a bad idea to base any decisions on this command."""
    
    if var.PHASE in ("gunduz", "gece") and nick not in var.list_players():
        cli.notice(nick, "You may not use this command right now.")
        return
    
    cli.msg(chan, "\2{0}\2 tosses a coin into the air...".format(nick))
    var.LOGGER.logMessage("{0} tosses a coin into the air...".format(nick))
    coin = random.choice(["heads", "tails"])
    specialty = random.randrange(0,10)
    if specialty == 0:
        coin = "its side"
    if specialty == 1:
        coin = botconfig.NICK
    cmsg = "The coin lands on \2{0}\2.".format(coin)
    cli.msg(chan, cmsg)
    var.LOGGER.logMessage(cmsg)

@cmd("pony")
def pony(cli, nick, chan, rest):
    """For entertaining bronies."""

    if var.PHASE in ("gunduz", "gece") and nick not in var.list_players():
        cli.notice(nick, "You may not use this command right now.")
        return

    cli.msg(chan, "\2{0}\2 tosses a pony into the air...".format(nick))
    var.LOGGER.logMessage("{0} tosses a pony into the air...".format(nick))
    pony = random.choice(["hoof", "plot"])
    cmsg = "The pony lands on \2{0}\2.".format(pony)
    cli.msg(chan, cmsg)
    var.LOGGER.logMessage(cmsg)

@cmd("zaman")
def timeleft(cli, nick, chan, rest):
    """Returns the time left until the next day/night transition."""
    
    if var.PHASE not in ("gunduz", "gece"):
        cli.notice(nick, "No game is currently running.")
        return

    if (chan != nick and var.LAST_TIME and
            var.LAST_TIME + timedelta(seconds=var.TIME_RATE_LIMIT) > datetime.now()):
        cli.notice(nick, ("This command is rate-limited. Please wait a while "
                          "before using it again."))
        return

    if chan != nick:
        var.LAST_TIME = datetime.now()

    if var.PHASE == "gunduz":
        if var.STARTED_DAY_PLAYERS <= var.SHORT_DAY_PLAYERS:
            remaining = int((var.SHORT_DAY_LIMIT_WARN +
                var.SHORT_DAY_LIMIT_CHANGE) - (datetime.now() -
                var.DAY_START_TIME).total_seconds())
        else:
            remaining = int((var.DAY_TIME_LIMIT_WARN +
                var.DAY_TIME_LIMIT_CHANGE) - (datetime.now() -
                var.DAY_START_TIME).total_seconds())
    else:
        remaining = int(var.NIGHT_TIME_LIMIT - (datetime.now() -
            var.NIGHT_START_TIME).total_seconds())
    
    #Check if timers are actually enabled
    if (var.PHASE == "gunduz") and ((var.STARTED_DAY_PLAYERS <= var.SHORT_DAY_PLAYERS and 
            var.SHORT_DAY_LIMIT_WARN == 0) or (var.DAY_TIME_LIMIT_WARN == 0 and
            var.STARTED_DAY_PLAYERS > var.SHORT_DAY_PLAYERS)):
        msg = "Day timers are currently disabled."
    elif var.PHASE == "gece" and var.NIGHT_TIME_LIMIT == 0:
        msg = "Night timers are currently disabled."
    else:
        msg = "{1} olmasina \x02{0[0]:0>2}:{0[1]:0>2}\x02 zaman var.".format(
            divmod(remaining, 60), "sabah" if var.PHASE == "gece" else "aksam")    

    if nick == chan:
        pm(cli, nick, msg)
    else:
        cli.msg(chan, msg)

@pmcmd("zaman")
def timeleft_pm(cli, nick, rest):
    timeleft(cli, nick, nick, rest)

@cmd("roles")
def listroles(cli, nick, chan, rest):
    """Display which roles are enabled and when"""

    old = var.ROLES_GUIDE.get(None)

    txt = ""

    pl = len(var.list_players()) + len(var.DEAD)
    if pl > 0:
        txt += '{0}: There are \u0002{1}\u0002 playing. '.format(nick, pl)

    for i,v in sorted({i:var.ROLES_GUIDE[i] for i in var.ROLES_GUIDE if i is not None}.items()):
        if (i <= pl):
            txt += BOLD
        txt += "[" + str(i) + "] "
        if (i <= pl):
            txt += BOLD
        for index, amt in enumerate(v):
            if amt - old[index] != 0:
                if amt > 1:
                    txt = txt + var.ROLE_INDICES[index] + "({0}), ".format(amt)
                else:
                    txt = txt + var.ROLE_INDICES[index] + ", "
        txt = txt[:-2] + " "
        old = v
    if chan == nick:
        pm(cli, nick, txt)
    else:
        cli.msg(chan, txt)

@pmcmd("roles")
def listroles_pm(cli, nick, rest):
    listroles(cli, nick, nick, rest)

@cmd("myrole")
def myrole(cli, nick, chan, rest):
    """Reminds you of which role you have."""
    if var.PHASE in ("yok", "oyun"):
        cli.notice(nick, "No game is currently running.")
        return
    
    ps = var.list_players()
    if nick not in ps:
        cli.notice(nick, "You're currently not playing.")
        return
    
    pm(cli, nick, "You are a \02{0}\02.".format(var.get_role(nick)))
    
    # Check for gun/bullets
    if nick in var.GUNNERS and var.GUNNERS[nick]:
        if var.GUNNERS[nick] == 1:
            pm(cli, nick, "You have a \02gun\02 with {0} {1}.".format(var.GUNNERS[nick], "bullet"))
        else:
            pm(cli, nick, "You have a \02gun\02 with {0} {1}.".format(var.GUNNERS[nick], "bullets"))
    elif nick in var.WOLF_GUNNERS and var.WOLF_GUNNERS[nick]:
        if var.WOLF_GUNNERS[nick] == 1:
            pm(cli, nick, "You have a \02gun\02 with {0} {1}.".format(var.WOLF_GUNNERS[nick], "bullet"))
        else:
            pm(cli, nick, "You have a \02gun\02 with {0} {1}.".format(var.WOLF_GUNNERS[nick], "bullets"))

@pmcmd("myrole")
def myrole_pm(cli, nick, rest):
    myrole(cli, nick, "", rest)
    
def aftergame(cli, rawnick, rest):
    """Schedule a command to be run after the game by someone."""
    chan = botconfig.CHANNEL
    nick = parse_nick(rawnick)[0]
    
    rst = re.split(" +", rest)
    cmd = rst.pop(0).lower().replace(botconfig.CMD_CHAR, "", 1).strip()

    if cmd in PM_COMMANDS.keys():
        def do_action():
            for fn in PM_COMMANDS[cmd]:
                fn(cli, rawnick, " ".join(rst))
    elif cmd in COMMANDS.keys():
        def do_action():
            for fn in COMMANDS[cmd]:
                fn(cli, rawnick, botconfig.CHANNEL, " ".join(rst))
    else:
        cli.notice(nick, "That command was not found.")
        return
        
    if var.PHASE == "yok":
        do_action()
        return
    
    cli.msg(chan, ("The command \02{0}\02 has been scheduled to run "+
                  "after this game by \02{1}\02.").format(cmd, nick))
    var.AFTER_FLASTGAME = do_action

    

@cmd("faftergame", admin_only=True, raw_nick=True)
def _faftergame(cli, nick, chan, rest):
    if not rest.strip():
        cli.notice(parse_nick(nick)[0], "Incorrect syntax for this command.")
        return
    aftergame(cli, nick, rest)
        
    
    
@pmcmd("faftergame", admin_only=True, raw_nick=True)
def faftergame(cli, nick, rest):
    _faftergame(cli, nick, botconfig.CHANNEL, rest)

@pmcmd("fghost", owner_only=True)
@cmd("fghost", owner_only=True)
def fghost(cli, nick, *rest):
    cli.msg(botconfig.CHANNEL, nick + " is the ghost!")
    cli.mode(botconfig.CHANNEL, "+v", nick)

@pmcmd("funghost", owner_only=True)
@cmd("funghost", owner_only=True)
def funghost(cli, nick, *rest):
    cli.mode(botconfig.CHANNEL, "-v", nick)
    
@pmcmd("flastgame", admin_only=True, raw_nick=True)
def flastgame(cli, nick, rest):
    """This command may be used in the channel or in a PM, and it disables starting or joining a game. !flastgame <optional-command-after-game-ends>"""
    rawnick = nick
    nick, _, __, cloak = parse_nick(rawnick)
    
    chan = botconfig.CHANNEL
    if var.PHASE != "oyun":
        if "oyna" in COMMANDS.keys():
            del COMMANDS["oyna"]
            cmd("oyna")(lambda *spam: cli.msg(chan, "This command has been disabled by an admin."))
            # manually recreate the command by calling the decorator function
        if "basla" in COMMANDS.keys():
            del COMMANDS["basla"]
            cmd("oyna")(lambda *spam: cli.msg(chan, "This command has been disabled by an admin."))
        
    cli.msg(chan, "Starting a new game has now been disabled by \02{0}\02.".format(nick))
    var.ADMIN_TO_PING = nick
    
    if rest.strip():
        aftergame(cli, rawnick, rest)
    
@cmd("flastgame", admin_only=True, raw_nick=True)
def _flastgame(cli, nick, chan, rest):
    flastgame(cli, nick, rest)
   
   
@cmd('gamestats', 'gstats')
def game_stats(cli, nick, chan, rest):
    """Gets the game stats for a given game size or lists game totals for all game sizes if no game size is given."""
    if (chan != nick and var.LAST_GSTATS and var.GSTATS_RATE_LIMIT and
            var.LAST_GSTATS + timedelta(seconds=var.GSTATS_RATE_LIMIT) >
            datetime.now()):
        cli.notice(nick, ('This command is rate-limited. Please wait a while '
                          'before using it again.'))
        return

    if chan != nick:
        var.LAST_GSTATS = datetime.now()

    if var.PHASE not in ('none', 'join'):
        cli.notice(nick, 'Wait until the game is over to view stats.')
        return
    
    # List all games sizes and totals if no size is given
    if not rest:
        if chan == nick:
            pm(cli, nick, var.get_game_totals())
        else:
            cli.msg(chan, var.get_game_totals())

        return

    # Check for invalid input
    rest = rest.strip()
    if not rest.isdigit() or int(rest) > var.MAX_PLAYERS or int(rest) < var.MIN_PLAYERS:
        cli.notice(nick, ('Please enter an integer between {} and '
                          '{}.').format(var.MIN_PLAYERS, var.MAX_PLAYERS))
        return
    
    # Attempt to find game stats for the given game size
    if chan == nick:
        pm(cli, nick, var.get_game_stats(int(rest)))
    else:
        cli.msg(chan, var.get_game_stats(int(rest)))


@pmcmd('gamestats', 'gstats')
def game_stats_pm(cli, nick, rest):
    game_stats(cli, nick, nick, rest)

    
@cmd('playerstats', 'pstats', 'player', 'p')
def player_stats(cli, nick, chan, rest):
    """Gets the stats for the given player and role or a list of role totals if no role is given."""
    if (chan != nick and var.LAST_PSTATS and var.PSTATS_RATE_LIMIT and
            var.LAST_PSTATS + timedelta(seconds=var.PSTATS_RATE_LIMIT) >
            datetime.now()):
        cli.notice(nick, ('This command is rate-limited. Please wait a while '
                          'before using it again.'))
        return

    if chan != nick:
        var.LAST_PSTATS = datetime.now()

    if var.PHASE not in ('none', 'join'):
        cli.notice(nick, 'Wait until the game is over to view stats.')
        return
    
    params = rest.split()

    # Check if we have enough parameters
    if params:
        user = params[0]
    else:
        user = nick

    # Find the player's account if possible
    if user in var.USERS:
        acc = var.USERS[user]['account']
        if acc == '*':
            if user == nick:
                cli.notice(nick, 'You are not identified with NickServ.')
            else:  
                cli.notice(nick, user + ' is not identified with NickServ.')

            return
    else:
        acc = user
    
    # List the player's total games for all roles if no role is given
    if len(params) < 2:
        if chan == nick:
            pm(cli, nick, var.get_player_totals(acc))
        else:
            cli.msg(chan, var.get_player_totals(acc))
    else:
        role = ' '.join(params[1:])  

        # Attempt to find the player's stats
        if chan == nick:
            pm(cli, nick, var.get_player_stats(acc, role))
        else:
            cli.msg(chan, var.get_player_stats(acc, role))
    

@pmcmd('playerstats', 'pstats', 'player', 'p')
def player_stats_pm(cli, nick, rest):
    player_stats(cli, nick, nick, rest)


@cmd("fpull", admin_only=True)
def fpull(cli, nick, chan, rest):
    output = None
    try:
        output = subprocess.check_output(('git', 'pull'), stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        pm(cli, nick, '{0}: {1}'.format(type(e), e))
        #raise

    if output:
        for line in output.splitlines():
            pm(cli, nick, line.decode('utf-8'))
    else:
        pm(cli, nick, '(no output)')

@pmcmd("fpull", admin_only=True)
def fpull_pm(cli, nick, rest):
    fpull(cli, nick, nick, rest)

@pmcmd("fsend", admin_only=True)
def fsend(cli, nick, rest):
    print('{0} - {1} fsend - {2}'.format(time.strftime('%Y-%m-%dT%H:%M:%S%z'), nick, rest))
    cli.send(rest)

    
before_debug_mode_commands = list(COMMANDS.keys())
before_debug_mode_pmcommands = list(PM_COMMANDS.keys())

if botconfig.DEBUG_MODE or botconfig.ALLOWED_NORMAL_MODE_COMMANDS:

    @cmd("eval", owner_only = True)
    @pmcmd("eval", owner_only = True)
    def pyeval(cli, nick, *rest):
        rest = list(rest)
        if len(rest) == 2:
            chan = rest.pop(0)
        else:
            chan = nick
        try:
            a = str(eval(rest[0]))
            if len(a) < 500:
                cli.msg(chan, a)
            else:
                cli.msg(chan, a[0:500])
        except Exception as e:
            cli.msg(chan, str(type(e))+":"+str(e))
            
            
    
    @cmd("exec", owner_only = True)
    @pmcmd("exec", owner_only = True)
    def py(cli, nick, *rest):
        rest = list(rest)
        if len(rest) == 2:
            chan = rest.pop(0)
        else:
            chan = nick
        try:
            exec(rest[0])
        except Exception as e:
            cli.msg(chan, str(type(e))+":"+str(e))

            

    @cmd("revealroles", admin_only=True)
    def revroles(cli, nick, chan, rest):
        if var.PHASE != "yok":
            cli.msg(chan, str(var.ROLES))
        if var.PHASE in ('night','day'):
            cli.msg(chan, "Cursed: "+str(var.CURSED))
            cli.msg(chan, "Gunners: "+str(list(var.GUNNERS.keys())))
        
        
    @cmd("fgame", admin_only=True)
    def game(cli, nick, chan, rest):
        pl = var.list_players()
        if var.PHASE == "yok":
            cli.notice(nick, "No game is currently running.")
            return
        if var.PHASE != "join":
            cli.notice(nick, "Werewolf is already in play.")
            return
        if nick not in pl:
            cli.notice(nick, "You're currently not playing.")
            return
        rest = rest.strip().lower()
        if rest:
            if cgamemode(cli, *re.split(" +",rest)):
                cli.msg(chan, ("\u0002{0}\u0002 has changed the "+
                                "game settings successfully.").format(nick))
    
    def fgame_help(args = ""):
        args = args.strip()
        if not args:
            return "Available game mode setters: "+ ", ".join(var.GAME_MODES.keys())
        elif args in var.GAME_MODES.keys():
            return var.GAME_MODES[args].__doc__
        else:
            return "Game mode setter {0} not found.".format(args)

    game.__doc__ = fgame_help


    # DO NOT MAKE THIS A PMCOMMAND ALSO
    @cmd("force", admin_only=True)
    def forcepm(cli, nick, chan, rest):
        rst = re.split(" +",rest)
        if len(rst) < 2:
            cli.msg(chan, "The syntax is incorrect.")
            return
        who = rst.pop(0).strip()
        if not who or who == botconfig.NICK:
            cli.msg(chan, "That won't work.")
            return
        if not is_fake_nick(who):
            ul = list(var.USERS.keys())
            ull = [u.lower() for u in ul]
            if who.lower() not in ull:
                cli.msg(chan, "This can only be done on fake nicks.")
                return
            else:
                who = ul[ull.index(who.lower())]
        cmd = rst.pop(0).lower().replace(botconfig.CMD_CHAR, "", 1)
        did = False
        if PM_COMMANDS.get(cmd) and not PM_COMMANDS[cmd][0].owner_only:
            if (PM_COMMANDS[cmd][0].admin_only and nick in var.USERS and 
                not is_admin(var.USERS[nick]["cloak"])):
                # Not a full admin
                cli.notice(nick, "Only full admins can force an admin-only command.")
                return
                
            for fn in PM_COMMANDS[cmd]:
                if fn.raw_nick:
                    continue
                fn(cli, who, " ".join(rst))
                did = True
            if did:
                cli.msg(chan, "Operation successful.")
            else:
                cli.msg(chan, "Not possible with this command.")
            #if var.PHASE == "gece":   <-  Causes problems with night starting twice.
            #    chk_nightdone(cli)
        elif COMMANDS.get(cmd) and not COMMANDS[cmd][0].owner_only:
            if (COMMANDS[cmd][0].admin_only and nick in var.USERS and 
                not is_admin(var.USERS[nick]["cloak"])):
                # Not a full admin
                cli.notice(nick, "Only full admins can force an admin-only command.")
                return
                
            for fn in COMMANDS[cmd]:
                if fn.raw_nick:
                    continue
                fn(cli, who, chan, " ".join(rst))
                did = True
            if did:
                cli.msg(chan, "Operation successful.")
            else:
                cli.msg(chan, "Not possible with this command.")
        else:
            cli.msg(chan, "That command was not found.")
            
            
    @cmd("rforce", admin_only=True)
    def rforcepm(cli, nick, chan, rest):
        rst = re.split(" +",rest)
        if len(rst) < 2:
            cli.msg(chan, "The syntax is incorrect.")
            return
        who = rst.pop(0).strip().lower()
        who = who.replace("_", " ")
        
        if (who not in var.ROLES or not var.ROLES[who]) and (who != "avci"
            or var.PHASE in ("yok", "join")):
            cli.msg(chan, nick+": invalid role")
            return
        elif who == "avci":
            tgt = list(var.GUNNERS.keys())
        else:
            tgt = var.ROLES[who]

        cmd = rst.pop(0).lower().replace(botconfig.CMD_CHAR, "", 1)
        if PM_COMMANDS.get(cmd) and not PM_COMMANDS[cmd][0].owner_only:
            if (PM_COMMANDS[cmd][0].admin_only and nick in var.USERS and 
                not is_admin(var.USERS[nick]["cloak"])):
                # Not a full admin
                cli.notice(nick, "Only full admins can force an admin-only command.")
                return
        
            for fn in PM_COMMANDS[cmd]:
                for guy in tgt[:]:
                    fn(cli, guy, " ".join(rst))
            cli.msg(chan, "Operation successful.")
            #if var.PHASE == "gece":   <-  Causes problems with night starting twice.
            #    chk_nightdone(cli)
        elif cmd.lower() in COMMANDS.keys() and not COMMANDS[cmd][0].owner_only:
            if (COMMANDS[cmd][0].admin_only and nick in var.USERS and 
                not is_admin(var.USERS[nick]["cloak"])):
                # Not a full admin
                cli.notice(nick, "Only full admins can force an admin-only command.")
                return
        
            for fn in COMMANDS[cmd]:
                for guy in tgt[:]:
                    fn(cli, guy, chan, " ".join(rst))
            cli.msg(chan, "Operation successful.")
        else:
            cli.msg(chan, "That command was not found.")



    @cmd("frole", admin_only=True)
    def frole(cli, nick, chan, rest):
        rst = re.split(" +",rest)
        if len(rst) < 2:
            cli.msg(chan, "The syntax is incorrect.")
            return
        who = rst.pop(0).strip()
        rol = " ".join(rst).strip()
        ul = list(var.USERS.keys())
        ull = [u.lower() for u in ul]
        if who.lower() not in ull:
            if not is_fake_nick(who):
                cli.msg(chan, "Could not be done.")
                cli.msg(chan, "The target needs to be in this channel or a fake name.")
                return
        if not is_fake_nick(who):
            who = ul[ull.index(who.lower())]
        if who == botconfig.NICK or not who:
            cli.msg(chan, "No.")
            return
        if rol not in var.ROLES.keys():
            pl = var.list_players()
            if var.PHASE not in ("gece", "gunduz"):
                cli.msg(chan, "This is only allowed in game.")
                return
            if rol.startswith("avci"):
                rolargs = re.split(" +",rol, 1)
                if len(rolargs) == 2 and rolargs[1].isdigit():
                    if len(rolargs[1]) < 7:
                        var.GUNNERS[who] = int(rolargs[1])
                        var.WOLF_GUNNERS[who] = int(rolargs[1])
                    else:
                        var.GUNNERS[who] = 999
                        var.WOLF_GUNNERS[who] = 999
                else:
                    var.GUNNERS[who] = math.ceil(var.SHOTS_MULTIPLIER * len(pl))
                if who not in pl:
                    var.ROLES["villager"].append(who)
            elif rol == "cursed villager":
                if who not in var.CURSED:
                    var.CURSED.append(who)
                if who not in pl:
                    var.ROLES["villager"].append(who)
            else:
                cli.msg(chan, "Not a valid role.")
                return
            cli.msg(chan, "Operation successful.")
            return
        if who in var.list_players():
            var.del_player(who)
        var.ROLES[rol].append(who)
        cli.msg(chan, "Operation successful.")
        if var.PHASE not in ('none','join'):
            chk_win(cli)

            
if botconfig.ALLOWED_NORMAL_MODE_COMMANDS and not botconfig.DEBUG_MODE:
    for comd in list(COMMANDS.keys()):
        if (comd not in before_debug_mode_commands and 
            comd not in botconfig.ALLOWED_NORMAL_MODE_COMMANDS):
            del COMMANDS[comd]
    for pmcomd in list(PM_COMMANDS.keys()):
        if (pmcomd not in before_debug_mode_pmcommands and
            pmcomd not in botconfig.ALLOWED_NORMAL_MODE_COMMANDS):
            del PM_COMMANDS[pmcomd]
