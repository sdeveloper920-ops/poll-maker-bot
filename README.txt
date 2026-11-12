POLL MAKER BOT PREMIUM - TELEGRAM + WEB ADMIN

FEATURES
Telegram bot:
- /start menu and guided poll creation
- Regular Poll and Quiz Poll
- Anonymous / public voting
- Multiple answers for regular polls
- Preview, publish and cancel draft
- Publish polls to a group/channel using @username or numeric chat ID

Web admin:
- Secure admin login using environment variables
- Dashboard and poll history
- Create and publish regular/quiz polls
- User list with block/unblock
- Broadcast message to users who have started the bot
- Close published polls

DEPLOYMENT (Render)
1. Create a Telegram bot using @BotFather and copy its token.
2. Upload all files in this folder to a GitHub repository.
3. Create a Render Web Service connected to the repository.
4. Build command: pip install -r requirements.txt
5. Start command: python app.py
6. Add Environment Variables:
   BOT_TOKEN = token from BotFather
   ADMIN_USERNAME = your admin username
   ADMIN_PASSWORD = a strong unique password
   SECRET_KEY = a long random secret
   DB_PATH = /var/data/polls.db (only after attaching a persistent disk)
7. If using SQLite on Render, attach a persistent disk mounted at /var/data.
8. Deploy. Open the service URL to access the admin login.
9. Add the bot as an administrator to the target group/channel and grant permission to send polls.

IMPORTANT
- Keep BOT_TOKEN, ADMIN_PASSWORD and SECRET_KEY private.
- Run only one service instance. Telegram long polling must not run in multiple instances.
- SQLite requires persistent storage for data to survive redeploys.
- The bot stores users only after they press /start or send a message.
- Broadcast is subject to Telegram limits and users must have started the bot.
- This is a starter implementation; production deployment should use PostgreSQL, a separate worker, CSRF protection, rate limits, and stronger admin authentication.
- Quiz correct option index in the web panel starts at 0; in Telegram bot conversation it starts at 1.
- Make sure your bot has admin rights in target channels/groups before publishing.
