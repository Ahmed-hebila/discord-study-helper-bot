import os
import json
import sqlite3
import logging
import asyncio
import datetime
from urllib.parse import urlparse, parse_qs

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai

# === السيرفر الوهمي عشان البوت يفضل شغال 24/7 ===
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is awake and running!"

def run():
    # Render بيستخدم بورت 8080 أو 10000 غالباً
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()
# ==========================================

# ==========================================
# Configuration & Setup
# ==========================================
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ==========================================
# Layer 1: Database Management (SQLite)
# ==========================================
class DatabaseManager:
    def __init__(self, db_path="course_bot.db"):
        self.db_path = db_path
        self._initialize_tables()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_tables(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    playlist_url TEXT,
                    current_index INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active'
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS playlist_videos (
                    user_id INTEGER,
                    video_index INTEGER,
                    video_id TEXT,
                    video_url TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            ''')
            conn.commit()

    def set_user_playlist(self, user_id: int, channel_id: int, playlist_url: str, videos: list):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
            cursor.execute('DELETE FROM playlist_videos WHERE user_id = ?', (user_id,))
            
            cursor.execute('''
                INSERT INTO users (user_id, channel_id, playlist_url, current_index, status)
                VALUES (?, ?, ?, 0, 'active')
            ''', (user_id, channel_id, playlist_url))
            
            for index, video in enumerate(videos):
                cursor.execute('''
                    INSERT INTO playlist_videos (user_id, video_index, video_id, video_url)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, index, video['id'], video['url']))
            conn.commit()

    def get_active_users(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE status = 'active'")
            return cursor.fetchall()

    def get_video_for_user(self, user_id: int, index: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM playlist_videos WHERE user_id = ? AND video_index = ?", (user_id, index))
            return cursor.fetchone()

    def advance_user_progress(self, user_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET current_index = current_index + 1 WHERE user_id = ?", (user_id,))
            conn.commit()

    def complete_course(self, user_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET status = 'completed' WHERE user_id = ?", (user_id,))
            conn.commit()

# ==========================================
# Layer 2: External Services Integration
# ==========================================
class YouTubeService:
    def __init__(self, api_key: str):
        self.youtube = build('youtube', 'v3', developerKey=api_key)

    def extract_playlist_id(self, url: str) -> str:
        parsed_url = urlparse(url)
        if parsed_url.hostname in ['www.youtube.com', 'youtube.com', 'youtu.be']:
            return parse_qs(parsed_url.query).get('list', [None])[0]
        return None

    def get_playlist_videos(self, playlist_id: str) -> list:
        videos = []
        next_page_token = None
        while True:
            request = self.youtube.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()
            for item in response['items']:
                video_id = item['snippet']['resourceId']['videoId']
                videos.append({
                    'id': video_id,
                    'url': f"https://www.youtube.com/watch?v={video_id}"
                })
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
        return videos

    def get_transcript(self, video_id: str) -> str:
        try:
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
            full_text = " ".join([t['text'] for t in transcript_list])
            return full_text[:15000] 
        except Exception as e:
            logging.error(f"Transcript error for {video_id}: {e}")
            return None

class QuizService:
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-1.5-flash')

    async def generate_quiz(self, transcript: str) -> dict:
        prompt = f"""
        Based on the following video transcript, generate ONE multiple-choice question to test if the user watched it.
        Return ONLY a valid JSON object in this exact format, with no markdown formatting or extra text:
        {{
            "question": "The question text here?",
            "options": ["Option A", "Option B", "Option C"],
            "correct_index": 0 
        }}
        Make sure the correct_index is an integer (0, 1, or 2) corresponding to the correct option.
        
        Transcript:
        {transcript}
        """
        try:
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            raw_text = response.text.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:-3].strip()
            elif raw_text.startswith("```"):
                raw_text = raw_text[3:-3].strip()
            return json.loads(raw_text)
        except Exception as e:
            logging.error(f"Quiz generation failed: {e}")
            return None

# ==========================================
# Layer 3: Discord UI & Bot Logic
# ==========================================
class QuizView(discord.ui.View):
    def __init__(self, user_id: int, quiz_data: dict, db_manager: DatabaseManager):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.correct_index = quiz_data.get('correct_index', 0)
        self.db = db_manager

        for i, option_text in enumerate(quiz_data.get('options', [])):
            button = discord.ui.Button(label=option_text[:80], style=discord.ButtonStyle.primary, custom_id=f"quiz_btn_{user_id}_{i}")
            button.callback = self.create_callback(i)
            self.add_item(button)

    def create_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("هذا الاختبار ليس لك!", ephemeral=True)

            if index == self.correct_index:
                self.db.advance_user_progress(self.user_id)
                for item in self.children:
                    item.disabled = True
                await interaction.response.edit_message(content="✅ **إجابة صحيحة!** تم اجتياز فيديو اليوم بنجاح، سيتم إرسال الفيديو القادم غداً.", view=self)
            else:
                await interaction.response.send_message("❌ إجابة خاطئة! راجع الفيديو وحاول مرة أخرى.", ephemeral=True)
        return callback

class NoTranscriptView(discord.ui.View):
    def __init__(self, user_id: int, db_manager: DatabaseManager):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.db = db_manager

    @discord.ui.button(label="لقد شاهدت الفيديو", style=discord.ButtonStyle.success)
    async def mark_watched(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("هذا ليس لك!", ephemeral=True)
            
        self.db.advance_user_progress(self.user_id)
        for item in self.children:
            item.disabled = True
            
        await interaction.response.edit_message(content="✅ تم تسجيل المشاهدة! الفيديو القادم غداً.", view=self)

class CourseBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.db = DatabaseManager()
        self.youtube = YouTubeService(os.getenv("YOUTUBE_API_KEY"))
        self.quiz = QuizService(os.getenv("GEMINI_API_KEY"))

    async def setup_hook(self):
        await self.tree.sync()
        self.daily_scheduler.start()
        logging.info("Bot is ready and scheduler started.")

    @tasks.loop(time=datetime.time(hour=10, minute=0, tzinfo=datetime.timezone.utc))
    async def daily_scheduler(self):
        logging.info("Running daily course scheduler...")
        active_users = self.db.get_active_users()
        
        for user_row in active_users:
            user_id = user_row['user_id']
            channel_id = user_row['channel_id']
            current_index = user_row['current_index']
            
            video = self.db.get_video_for_user(user_id, current_index)
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            
            if not video:
                self.db.complete_course(user_id)
                if channel:
                    await channel.send(f"<@{user_id}> 🎉 **ألف مبروك!** لقد أنهيت قائمة التشغيل بنجاح. استخدم `/start_course` للبدء في واحدة جديدة.")
                continue

            if channel:
                await self.send_daily_video(channel, user_id, video['video_id'], video['video_url'])

    async def send_daily_video(self, channel, user_id: int, video_id: str, video_url: str):
        message = await channel.send(f"<@{user_id}> 📚 **إليك فيديو اليوم!**\nشاهد الفيديو ثم أجب على الاختبار بالأسفل لفتح فيديو الغد:\n{video_url}")
        
        transcript = self.youtube.get_transcript(video_id)
        if not transcript:
            view = NoTranscriptView(user_id, self.db)
            await channel.send("*(ملاحظة: لا يوجد نص متوفر لهذا الفيديو لعمل اختبار بالذكاء الاصطناعي. يرجى تأكيد المشاهدة يدوياً.)*", view=view)
            return

        quiz_data = await self.quiz.generate_quiz(transcript)
        if quiz_data:
            view = QuizView(user_id, quiz_data, self.db)
            await channel.send(f"**الاختبار:** {quiz_data['question']}", view=view)
        else:
            view = NoTranscriptView(user_id, self.db)
            await channel.send("*(فشل في توليد الاختبار اليوم. يرجى تأكيد المشاهدة يدوياً.)*", view=view)

bot = CourseBot()

@bot.tree.command(name="start_course", description="ابدأ كورس يومي من قائمة تشغيل يوتيوب")
async def start_course(interaction: discord.Interaction, playlist_url: str):
    await interaction.response.defer()
    
    playlist_id = bot.youtube.extract_playlist_id(playlist_url)
    if not playlist_id:
        return await interaction.followup.send("❌ رابط غير صالح. تأكد أن الرابط يحتوي على `list=`.")

    try:
        videos = bot.youtube.get_playlist_videos(playlist_id)
        if not videos:
            return await interaction.followup.send("❌ لم أتمكن من العثور على فيديوهات. تأكد أن قائمة التشغيل عامة (Public).")
            
        bot.db.set_user_playlist(interaction.user.id, interaction.channel_id, playlist_url, videos)
        
        await interaction.followup.send(f"✅ **تم بدء الكورس!**\nتم استخراج {len(videos)} فيديو. سأرسل لك الفيديو الأول الآن، والباقي يومياً الساعة 10:00 صباحاً (بتوقيت جرينتش).")
        
        first_video = videos[0]
        await bot.send_daily_video(interaction.channel, interaction.user.id, first_video['id'], first_video['url'])

    except Exception as e:
        logging.error(f"Error starting course: {e}")
        await interaction.followup.send("❌ حدث خطأ أثناء جلب البيانات. راجع السجلات.")

if __name__ == "__main__":
    keep_alive() # تشغيل السيرفر الوهمي
    bot.run(os.getenv("DISCORD_TOKEN"))