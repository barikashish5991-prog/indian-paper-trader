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
    except (OSError,smtplib.SMTPException):
        raise SafetyError('Email delivery failed; queued messages retained for retry. Check SMTP settings and provider access.') from None


def deliver(db, now, sender=send_message):
    initialize(db)
    count=0
    for row in db.execute('SELECT id,subject,body FROM outbox WHERE delivered_at IS NULL ORDER BY created_at,id').fetchall():
        with db: db.execute('UPDATE outbox SET attempts=attempts+1 WHERE id=?',(row[0],))
        sender(row[1],row[2],row[0])
        with db: db.execute('UPDATE outbox SET delivered_at=? WHERE id=?',(now,row[0]))
        count+=1
    return count
