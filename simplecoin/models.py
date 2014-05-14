import calendar
import logging
import json
import smtplib
from email.mime.text import MIMEText
from collections import namedtuple

from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy.schema import CheckConstraint
from sqlalchemy.ext.declarative import AbstractConcreteBase, declared_attr
from sqlalchemy.dialects.postgresql import HSTORE, ARRAY

from cryptokit import bits_to_difficulty
from .model_lib import base
from . import db, cache, sig_round


class Blob(base):
    """ Used to store misc single value blobs of data, such as the current
    block height and difficulty. """
    key = db.Column(db.String, primary_key=True)
    data = db.Column(HSTORE, default=dict)


class Block(base):
    """ This class stores metadata on all blocks found by the pool """
    # the hash of the block for orphan checking
    hash = db.Column(db.String, primary_key=True)
    height = db.Column(db.Integer, nullable=False)
    # User who discovered block
    user = db.Column(db.String)
    # When block was found
    found_at = db.Column(db.DateTime, default=datetime.utcnow)
    # # Time started on block
    time_started = db.Column(db.DateTime, nullable=False)
    # Is block now orphaned?
    orphan = db.Column(db.Boolean, default=False)
    # Is the block matured?
    mature = db.Column(db.Boolean, default=False)
    # Total shares that were required to solve the block
    shares_to_solve = db.Column(db.BigInteger)
    # Block value (does not include transaction fees recieved)
    total_value = db.Column(db.BigInteger)
    # Associated transaction fees
    transaction_fees = db.Column(db.BigInteger)
    # total going to pool from fees
    donated = db.Column(db.BigInteger)
    bonus_payed = db.Column(db.BigInteger)
    # Difficulty of block when solved
    bits = db.Column(db.String(8), nullable=False)
    # Average difficulty of last x (default 500..) blocks when it was solved
    difficulty_avg = db.Column(db.Integer)
    # the last share id that was processed when the block was entered.
    # used as a marker for calculating last n shares
    last_share_id = db.Column(db.BigInteger, db.ForeignKey('share.id'))
    last_share = db.relationship('Share', foreign_keys=[last_share_id])
    # have payments been generated for it?
    processed = db.Column(db.Boolean, default=False)
    # is this a merge mined block, or a core block?
    merged_type = db.Column(db.String, default=None)
    worker = db.Column(db.String, default=None)

    standard_join = ['status', 'explorer_link', 'luck', 'total_value_float',
                     'difficulty', 'duration', 'found_at', 'time_started']

    @property
    def status(self):
        if self.mature:
            return "Mature"
        if self.orphan:
            return "Orphan"
        confirms = self.confirms_remaining
        if confirms is not None:
            return "{} Confirms Remaining".format(confirms)
        else:
            return "Pending confirmation"

    @classmethod
    def create(cls, user, height, total_value, transaction_fees, bits, hash,
               time_started, merged_type=None, worker=None):
        share = Share.query.order_by(Share.id.desc()).first()
        block = cls(user=user,
                    height=height,
                    total_value=total_value,
                    transaction_fees=transaction_fees,
                    bits=bits,
                    last_share=share,
                    hash=hash,
                    time_started=time_started,
                    difficulty_avg=cache.get('difficulty_avg'),
                    merged_type=merged_type,
                    worker=worker)
        # add and flush
        db.session.add(block)
        return block

    @property
    def explorer_link(self):
        if not self.merged_type:
            return current_app.config['block_link_prefix'] + self.hash
        else:
            cfg = current_app.config['merged_cfg'][self.merged_type]
            return cfg['block_link_prefix'] + self.hash

    @property
    def luck(self):
        return ((self.difficulty * (2 ** 16)) / self.shares_to_solve) * 100

    @property
    def total_value_float(self):
        return self.total_value / 100000000.0

    @property
    def difficulty(self):
        return bits_to_difficulty(self.bits)

    @property
    def timestamp(self):
        return calendar.timegm(self.found_at.utctimetuple())

    @property
    def duration(self):
        seconds = round((self.found_at - self.time_started).total_seconds())
        formatted_time = timedelta(seconds=seconds)
        return formatted_time

    @property
    def confirms_remaining(self):
        bh = cache.get('blockheight')
        if not bh or self.merged_type:
            return None
        confirms_req = current_app.config['block_mature_confirms']
        # prevent displaying negative confirms
        return max(0, confirms_req - (bh - self.height))


class Share(base):
    """ This class generates a table containing every share accepted for a
    round """
    id = db.Column(db.BigInteger, primary_key=True)
    user = db.Column(db.String)
    shares = db.Column(db.Integer)

    @classmethod
    def create(cls, user, shares):
        share = cls(user=user, shares=shares)
        db.session.add(share)
        return share


class Transaction(base):
    txid = db.Column(db.String, primary_key=True)
    confirmed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    merged_type = db.Column(db.String)

    @classmethod
    def create(cls, txid, merged_type=None):
        trans = cls(txid=txid, merged_type=merged_type)
        db.session.add(trans)
        return trans


class Status(base):
    """ This class generates a table containing every share accepted for a
    round """
    user = db.Column(db.String, primary_key=True)
    worker = db.Column(db.String, primary_key=True)
    status = db.Column(db.String)
    time = db.Column(db.DateTime)

    @property
    def parsed_status(self):
        return json.loads(self.status)

    def pretty_json(self, gpu=0):
        return json.dumps(json.loads(self.status)['gpus'][gpu], indent=4, sort_keys=True)

    @property
    def stale(self):
        ten_min_ago = datetime.utcnow() - timedelta(minutes=10)
        if ten_min_ago >= self.time:
            return True
        else:
            return False


class Threshold(base):
    """ Notification Thresholds for workers """
    user = db.Column(db.String, primary_key=True)
    worker = db.Column(db.String, primary_key=True)
    temp_thresh = db.Column(db.Integer)
    offline_thresh = db.Column(db.Integer)
    hashrate_thresh = db.Column(db.Integer)
    # Is there an error with any of these thresholds
    hashrate_err = db.Column(db.Boolean, default=False)
    temp_err = db.Column(db.Boolean, default=False)
    offline_err = db.Column(db.Boolean, default=False)
    # whether we should notify of the condition becoming fixed
    green_notif = db.Column(db.Boolean, default=True)
    emails = db.Column(ARRAY(db.String))

    def report_condition(self, message, typ, new_state):
        db.session.refresh(self, lockmode='update')
        # we got beat in a race condition...
        if getattr(self, typ) == new_state:
            current_app.logger.info("Ignored sending of report_condition due "
                                    "to race condition resolution")
            return
        setattr(self, typ, new_state)

        # if we shouldn't notify of state going up
        if new_state and not self.green_notif:
            return
        current_app.logger.info("Reporting '{}' for worker {}; addr: {}"
                                .format(message, self.worker, self.user))

        # get all the events that happened for these addresses in the last hour
        hour_ago = datetime.utcnow() - timedelta(hours=1)
        events = (Event.query.filter_by(worker=self.worker, user=self.user).
                  filter(Event.address.in_(self.emails)).
                  filter(Event.time >= hour_ago).all())

        try:
            econf = current_app.config['email']
            if not econf.get('enabled', True):
                current_app.logger.warn("Skipping actual email send because disabled!")
                return True

            send_addr = econf['send_address']
            host = smtplib.SMTP(
                host=econf['server'],
                port=econf['port'],
                local_hostname=econf['ehlo'],
                timeout=econf['timeout'])
            host.set_debuglevel(econf['debug'])
            if econf['tls']:
                host.starttls()
            if econf['ehlo']:
                host.ehlo()

            host.login(econf['username'], econf['password'])
            email_limit = current_app.config.get('emails_per_hour_cap', 6)
            # Send the message via our own SMTP server, but don't include the
            # envelope header.
            for address in self.emails:
                count = len([a for a in events if a.address == address])
                if count <= email_limit:
                    msg = MIMEText('http://simpledoge.com/{}'.format(self.user))
                    msg['Subject'] = message
                    msg['From'] = 'Simple Doge <simpledogepool@gmail.com>'
                    msg['To'] = address
                    host.sendmail(send_addr, address, msg.as_string())
                else:
                    current_app.logger.info(
                        "Not sending email to {} because over limit"
                        .format(address, email_limit))
                ev = Event(user=self.user, worker=self.worker, address=address)
                db.session.add(ev)
        except smtplib.SMTPException:
            current_app.logger.warn('Email unable to send', exc_info=True)
            return False
        else:
            host.quit()

        return True


class Event(base):
    time = db.Column(db.DateTime, primary_key=True, default=datetime.utcnow)
    user = db.Column(db.String, primary_key=True)
    worker = db.Column(db.String, primary_key=True)
    address = db.Column(db.String, primary_key=True)


class MergeAddress(base):
    user = db.Column(db.String, primary_key=True)
    merged_type = db.Column(db.String, primary_key=True)
    merge_address = db.Column(db.String)


class DonationPercent(base):
    user = db.Column(db.String, primary_key=True)
    perc = db.Column(db.Float)


class Transfer(AbstractConcreteBase, base):
    """ Represents a users payout for a single round """
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String)
    amount = db.Column(db.BigInteger, CheckConstraint('amount>0', 'min_payout_amount'))
    # allows us to lock a transfer while doing a payout. prevents double
    # spending
    locked = db.Column(db.Boolean, default=False, server_default="FALSE")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    merged_type = db.Column(db.String, default=None)

    @declared_attr
    def transaction_id(self):
        return db.Column(db.String, db.ForeignKey('transaction.txid'))

    @declared_attr
    def transaction(self):
        return db.relationship('Transaction')

    @property
    def status(self):
        if self.transaction:
            if self.transaction.confirmed is True:
                return "Payout Transaction Confirmed"
            else:
                return "Payout Transaction Pending"
        elif self.block.orphan:
            return "Block Orphaned"
        elif not self.block.mature:

            confirms = self.block.confirms_remaining
            if confirms is not None:
                return "{} Block Confirms Remaining".format(confirms)
            else:
                return "Pending Block Confirmation"
        else:
            return "Payout Pending"

    @property
    def explorer_link(self):
        if not self.transaction_id:
            return
        if not self.merged_type:
            return current_app.config['transaction_link_prefix'] + self.transaction_id
        else:
            cfg = current_app.config['merged_cfg'][self.merged_type]
            return cfg['transaction_link_prefix'] + self.transaction_id

    @property
    def amount_float(self):
        return self.amount / 100000000.0


class Payout(Transfer):
    __tablename__ = "payout"
    blockhash = db.Column(db.String, db.ForeignKey('block.hash'))
    block = db.relationship('Block', foreign_keys=[blockhash])
    shares = db.Column(db.BigInteger)
    perc = db.Column(db.Float, default=0.0, server_default="0")
    perc_applied = db.Column(db.BigInteger, default=0, server_default="0")
    __mapper_args__ = {
        'polymorphic_identity': 'payout',
        'concrete': True
    }
    __table_args__ = (
        db.UniqueConstraint("user", "blockhash"),
    )

    standard_join = ['status', 'created_at', 'explorer_link',
                     'text_perc_applied', 'mined', 'amount_float', 'height',
                     'transaction_id']

    @property
    def text_perc_applied(self):
        if self.perc < 0:
            return "bonus of {}".format(sig_round(self.perc_applied * -1 / 100000000.0))
        else:
            return "donation of {}".format(sig_round(self.perc_applied / 100000000.0))

    @property
    def mined(self):
        return (self.amount + self.perc_applied) / 100000000.0

    @classmethod
    def create(cls, user, amount, block, shares, perc, perc_applied,
               merged_type=None):
        payout = cls(user=user, amount=amount, block=block, shares=shares,
                     perc=perc, perc_applied=perc_applied,
                     merged_type=merged_type)
        db.session.add(payout)
        return payout

    @property
    def timestamp(self):
        return calendar.timegm(self.created_at.utctimetuple())

    @property
    def height(self):
        return self.block.height

    @property
    def status(self):
        if self.transaction:
            if self.transaction.confirmed is True:
                return "Payout Transaction Confirmed"
            else:
                return "Payout Transaction Pending"
        elif self.block.orphan:
            return "Block Orphaned"
        elif not self.block.mature:

            confirms = self.block.confirms_remaining
            if confirms is not None:
                return "{} Block Confirms Remaining".format(confirms)
            else:
                return "Pending Block Confirmation"
        else:
            return "Payout Pending"


class BonusPayout(Transfer):
    __tablename__ = "bonus_payout"
    description = db.Column(db.String)
    blockhash = db.Column(db.String, db.ForeignKey('block.hash'))
    block = db.relationship('Block', foreign_keys=[blockhash])
    __mapper_args__ = {
        'polymorphic_identity': 'bonus_payout',
        'concrete': True
    }
    standard_join = ['status', 'created_at', 'explorer_link', 'amount_float',
                     'transaction_id']

    @classmethod
    def create(cls, user, amount, description, block, merged_type=None):
        bonus = cls(user=user, amount=amount, description=description,
                    block=block, merged_type=merged_type)
        db.session.add(bonus)
        return bonus


class SliceMixin(object):
    @classmethod
    def create(cls, user, value, time, worker):
        dt = cls.floor_time(time)
        slc = cls(user=user, value=value, time=dt, worker=worker)
        db.session.add(slc)
        return slc

    @classmethod
    def add_value(cls, user, value, time, worker):
        dt = cls.floor_time(time)
        slc = cls.query.with_lockmode('update').filter_by(
            user=user, time=dt, worker=worker).one()
        slc.value += value

    @classmethod
    def floor_time(cls, time):
        """ Changes an integer timestamp to the minute for which it falls in.
        Allows abstraction of create and add share logic for each time slice
        object. """
        if isinstance(time, datetime):
            time = calendar.timegm(time.utctimetuple())
        return datetime.utcfromtimestamp(
            (time // cls.slice_seconds) * cls.slice_seconds)

    @classmethod
    def compress(cls):
        """ Moves statistics that are past the `window` time into the next
        time slice size, effectively compressing the data. """
        # get the minute shares that are old enough to be compressed and
        # deleted
        recent = cls.floor_time(datetime.utcnow()) - cls.window
        # the five minute slice currently being processed
        current_slice = None
        # dictionary of lists keyed by user
        users = {}

        def create_upper():
            # add a time slice for each user in this pending period
            for key, slices in users.iteritems():
                new_val = cls.combine(*[slc.value for slc in slices])

                # put it in the database
                upper = cls.upper.query.filter_by(time=current_slice, **key._asdict()).with_lockmode('update').first()
                # wasn't in the db? create it
                if not upper:
                    dt = cls.floor_time(current_slice)
                    upper = cls.upper(time=dt, value=new_val, **key._asdict())
                    db.session.add(upper)
                else:
                    upper.value = cls.combine(upper.value, new_val)

                for slc in slices:
                    db.session.delete(slc)

        # traverse minute shares that are old enough in time order
        for slc in (cls.query.filter(cls.time < recent).
                    order_by(cls.time)):
            slice_time = cls.upper.floor_time(slc.time)

            if current_slice is None:
                current_slice = slice_time

            # we've encountered the next time slice, so commit the pending one
            if slice_time != current_slice:
                logging.debug("Processing slice " + str(current_slice))
                create_upper()
                users.clear()
                current_slice = slice_time

            # add the one min shares for this user the list of pending shares
            # to be grouped together
            key = slc.make_key()
            users.setdefault(key, [])
            users[key].append(slc)

        create_upper()


@classmethod
def average_combine(cls, *lst):
    """ Takes an iterable and combines the values. Usually either returns
    an average or a sum. Can assume at least one item in list """
    return sum(lst) / len(lst)


@classmethod
def sum_combine(cls, *lst):
    """ Takes a query list and combines the values. Usually either returns
    an average or a sum. Can assume at least one item in ql """
    return sum(lst)


class WorkerTimeSlice(AbstractConcreteBase, SliceMixin, base):
    """ An time abstracted data sample that pertains to a single worker.
    Currently used to represent accepted and rejected shares. """
    user = db.Column(db.String, primary_key=True)
    time = db.Column(db.DateTime, primary_key=True)
    worker = db.Column(db.String, primary_key=True)
    value = db.Column(db.Integer)

    combine = sum_combine
    key = namedtuple('Key', ['user', 'worker'])

    def make_key(self):
        return self.key(user=self.user, worker=self.worker)


class DeviceTimeSlice(AbstractConcreteBase, SliceMixin, base):
    """ An time abstracted data sample that pertains to a single workers single
    device.  Currently used to temperature and hashrate. """
    user = db.Column(db.String, primary_key=True)
    device = db.Column(db.Integer, primary_key=True)
    time = db.Column(db.DateTime, primary_key=True)
    worker = db.Column(db.String, primary_key=True)
    value = db.Column(db.Integer)

    combine = average_combine
    key = namedtuple('Key', ['user', 'worker', 'device'])

    def make_key(self):
        return self.key(user=self.user, worker=self.worker, device=self.device)


class TypeTimeSlice(AbstractConcreteBase, SliceMixin, base):
    """ An time abstracted data sample that pertains to a single workers single
    device.  Currently used to temperature and hashrate. """
    typ = db.Column(db.String, primary_key=True)
    time = db.Column(db.DateTime, primary_key=True)
    value = db.Column(db.Integer)

    combine = average_combine
    key = namedtuple('Key', ['typ'])

    def make_key(self):
        return self.key(typ=self.typ)


# Mixin classes the define time windows of generic timeslices
class OneMinute(object):
    window = timedelta(hours=1)
    slice = timedelta(minutes=1)
    slice_seconds = slice.total_seconds()


class OneHour(object):
    window = timedelta(days=30)
    slice = timedelta(hours=1)
    slice_seconds = slice.total_seconds()


class FiveMinute(object):
    window = timedelta(days=1)
    slice = timedelta(minutes=5)
    slice_seconds = slice.total_seconds()


# All of our accepted share timeslices
class OneHourShare(WorkerTimeSlice, OneHour):
    __tablename__ = 'one_hour_share'
    __mapper_args__ = {
        'polymorphic_identity': 'one_hour_share',
        'concrete': True
    }


class FiveMinuteShare(WorkerTimeSlice, FiveMinute):
    __tablename__ = 'five_minute_share'
    upper = OneHourShare
    __mapper_args__ = {
        'polymorphic_identity': 'five_minute_share',
        'concrete': True
    }


class OneMinuteShare(WorkerTimeSlice, OneMinute):
    __tablename__ = 'one_minute_share'
    upper = FiveMinuteShare
    __mapper_args__ = {
        'polymorphic_identity': 'one_minute_share',
        'concrete': True
    }


# All of our reject time slices
class OneHourReject(WorkerTimeSlice, OneHour):
    __tablename__ = 'one_hour_reject'
    __mapper_args__ = {
        'polymorphic_identity': 'one_hour_reject',
        'concrete': True
    }


class FiveMinuteReject(WorkerTimeSlice, FiveMinute):
    __tablename__ = 'five_minute_reject'
    upper = OneHourReject
    __mapper_args__ = {
        'polymorphic_identity': 'five_minute_reject',
        'concrete': True
    }


class OneMinuteReject(WorkerTimeSlice, OneMinute):
    __tablename__ = 'one_minute_reject'
    upper = FiveMinuteReject
    __mapper_args__ = {
        'polymorphic_identity': 'one_minute_reject',
        'concrete': True
    }


# Temperature time slices
class OneHourTemperature(DeviceTimeSlice, OneHour):
    __tablename__ = 'one_hour_temperature'
    __mapper_args__ = {
        'polymorphic_identity': 'one_hour_temperature',
        'concrete': True
    }


class FiveMinuteTemperature(DeviceTimeSlice, FiveMinute):
    __tablename__ = 'five_minute_temperature'
    upper = OneHourTemperature
    __mapper_args__ = {
        'polymorphic_identity': 'five_minute_temperature',
        'concrete': True
    }


class OneMinuteTemperature(DeviceTimeSlice, OneMinute):
    __tablename__ = 'one_minute_temperature'
    upper = FiveMinuteTemperature
    __mapper_args__ = {
        'polymorphic_identity': 'one_minute_temperature',
        'concrete': True
    }


# Hashrate timeslices
class OneHourHashrate(DeviceTimeSlice, OneHour):
    __tablename__ = 'one_hour_hashrate'
    __mapper_args__ = {
        'polymorphic_identity': 'one_hour_hashrate',
        'concrete': True
    }


class FiveMinuteHashrate(DeviceTimeSlice, FiveMinute):
    __tablename__ = 'five_minute_hashrate'
    upper = OneHourHashrate
    __mapper_args__ = {
        'polymorphic_identity': 'five_minute_hashrate',
        'concrete': True
    }


class OneMinuteHashrate(DeviceTimeSlice, OneMinute):
    __tablename__ = 'one_minute_hashrate'
    upper = FiveMinuteHashrate
    __mapper_args__ = {
        'polymorphic_identity': 'one_minute_hashrate',
        'concrete': True
    }


# Pool global attributes split up by type, such as worker count
class OneHourType(TypeTimeSlice, OneHour):
    __tablename__ = 'one_hour_type'
    __mapper_args__ = {
        'polymorphic_identity': 'one_hour_type',
        'concrete': True
    }


class FiveMinuteType(TypeTimeSlice, FiveMinute):
    __tablename__ = 'five_minute_type'
    upper = OneHourType
    __mapper_args__ = {
        'polymorphic_identity': 'five_minute_type',
        'concrete': True
    }


class OneMinuteType(TypeTimeSlice, OneMinute):
    __tablename__ = 'one_minute_type'
    upper = FiveMinuteType
    __mapper_args__ = {
        'polymorphic_identity': 'one_minute_type',
        'concrete': True
    }
