# Discord Study Helper Bot

A Discord bot that turns a public YouTube playlist into a daily study course. It sends one video at a time, creates a multiple-choice quiz from the video's transcript with Gemini, and stores each learner's progress in SQLite.

## How it works

1. Run `/start_course` with a public YouTube playlist URL.
2. The bot sends the first video immediately.
3. If the transcript is available, Gemini generates a three-option quiz. A correct answer unlocks the next video for the following day.
4. If a transcript or quiz cannot be generated, the bot offers a button to confirm that the video was watched.
5. The next lesson is scheduled for 10:00 UTC. The bot marks the course complete when the playlist ends.

Progress is saved in the local `course_bot.db` SQLite database.

## Tech stack

- Python and `discord.py` slash commands
- YouTube Data API for playlist videos
- YouTube Transcript API for captions
- Gemini for quiz generation
- SQLite for learner progress
- Flask keep-alive endpoint

## Requirements

- A Discord application and bot token
- A YouTube Data API key
- A Gemini API key
- Python and pip

## Run locally

Install the dependencies:

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
DISCORD_TOKEN=your_discord_bot_token
YOUTUBE_API_KEY=your_youtube_data_api_key
GEMINI_API_KEY=your_gemini_api_key
```

Invite the bot to a server with the required bot and application command permissions, then run:

```bash
python main.py
```

Keep API keys and bot tokens private. The bot needs network access to Discord, YouTube, and Gemini. Playlists should be public; quiz generation depends on video transcripts being available.
