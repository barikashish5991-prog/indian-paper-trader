"""Persistent email outbox. Transport is at-least-once, never exactly-once."""
import hashlib
import os
import smtplib
import ssl
from email.message import EmailMessage

from .core import SafetyError


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS outbox(
      id TEXT PRIMARY KEY, subject TEXT, body TEXT, created_at TEXT,
      delivered_at TEXT, attempts INTEGER NOT NULL DEFAULT 0)''')
    db.commit()


def queue(db, key, subject, body, now):
    with db:
        db.execute('INSERT OR IGNORE INTO outbox(id,subject,body,created_at) VALUES(?,?,?,?)',
                   (key,'[PAPER ONLY] '+subject,body+'\n\nSimulated trades only. No real orders were placed.',now))


def settings():
    names=('SMTP_HOST','SMTP_USER','SMTP_PASSWORD','EMAIL_FROM','ALERT_EMAIL')
    result={k:os.environ.get(k,'').strip() for k in names}
    if not all(result.values()): raise SafetyError('Email secrets are incomplete; configure SMTP and recipient in GitHub Actions secrets')
    if any('\n' in v or '\r' in v for v in result.values()): raise SafetyError('Invalid email configuration')
    # Gmail displays app passwords in groups separated by spaces.
    if result['SMTP_HOST'].lower() == 'smtp.gmail.com':
        result['SMTP_PASSWORD']=result['SMTP_PASSWORD'].replace(' ','')
    # One recipient only; no arbitrary address-list expansion.
    for key in ('EMAIL_FROM','ALERT_EMAIL'):
        value=result[key]
        if value.count('@')!=1 or any(c in value for c in '<>,; '): raise SafetyError('Use one plain email address per setting')
    return result


def send_message(subject, body, key):
    c=settings()
    message=EmailMessage()
    message['Subject']=subject
    message['From']=c['EMAIL_FROM']; message['To']=c['ALERT_EMAIL']
    message['Message-ID']='<'+hashlib.sha256(key.encode()).hexdigest()+'@paper-trial.local>'
    message.set_content(body)
    try:
        with smtplib.SMTP(c['SMTP_HOST'],587,timeout=20) as smtp:
            smtp.ehlo(); smtp.starttls(context=ssl.create_default_context()); smtp.ehlo()
            smtp.login(c['SMTP_USER'],c['SMTP_PASSWORD'])
            refused=smtp.send_message(message)
            if refused: raise SafetyError('Email recipient was refused')
    except smtplib.SMTPAuthenticationError:
        raise SafetyError('Email login rejected. Check that SMTP_USER matches the Google account that issued the app password and replace SMTP_PASSWORD if needed. Queued messages retained.') from None
    except smtplib.SMTPRecipientsRefused:
        raise SafetyError('Email recipient rejected. Check ALERT_EMAIL. Queued messages retained.') from None
    except smtplib.SMTPSenderRefused:
        raise SafetyError('Email sender rejected. Check EMAIL_FROM matches the authenticated account. Queued messages retained.') from None
    except ssl.SSLError:
        raise SafetyError('Email TLS connection failed. Queued messages retained.') from None
    except smtplib.SMTPResponseException as error:
        raise SafetyError('Email provider rejected the request (SMTP code '+str(error.smtp_code)+'). Queued messages retained.') from None
    except (OSError,smtplib.SMTPException):
        raise SafetyError('Email connection failed. Check SMTP host and provider availability. Queued messages retained.') from None


def deliver(db, now, sender=send_message):
    initialize(db)
    count=0
    for row in db.execute('SELECT id,subject,body FROM outbox WHERE delivered_at IS NULL ORDER BY created_at,id').fetchall():
        with db: db.execute('UPDATE outbox SET attempts=attempts+1 WHERE id=?',(row[0],))
        sender(row[1],row[2],row[0])
        with db: db.execute('UPDATE outbox SET delivered_at=? WHERE id=?',(now,row[0]))
        count+=1
    return count
