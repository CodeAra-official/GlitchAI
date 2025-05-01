import nest_asyncio
import asyncio
import logging
from telethon import TelegramClient, events, Button, types
import google.generativeai as genai
import os
from dotenv import load_dotenv
import requests
from io import BytesIO
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
import json
import sqlite3
from pathlib import Path
import re
import uuid
from collections import defaultdict
import base64
import time
import random

app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# Load environment variables
load_dotenv()

# Enable nest_asyncio
nest_asyncio.apply()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Get credentials
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
STABILITY_API_KEY = os.getenv("STABILITY_API_KEY")

# Validate credentials
if not all([API_ID, API_HASH, BOT_TOKEN, GEMINI_API_KEY]):
    raise ValueError("❌ Missing environment variables")

# Initialize clients
client = TelegramClient('bot_session', API_ID, API_HASH)
genai.configure(api_key=GEMINI_API_KEY)

# Set up Gemini model
model = genai.GenerativeModel('gemini-2.0-flash')

# Constants
BOT_VERSION = "3.5.0"  # Updated version
BOT_NAME = "GlitchAI"
COMPANY = "CodeAra"
DATE_UPDATE = "01-05-2025"
FOUNDER = "Wail Achouri"
BUILD_ID = "GlitchAI Emerald Edition"  # Updated build ID
MAX_FILE_SIZE = 50 * 1024 * 1024  # Increased to 50MB

# Menu state tracking
user_menu_state = {}  # Tracks which menu each user is currently viewing
active_messages = {}  # Tracks active menu messages for each user
conversation_contexts = {}  # Stores active conversation contexts
user_sessions = defaultdict(dict)  # Stores user session information

# Group settings
group_settings = {}  # Stores group-specific settings

# User customization settings
user_customization = {}  # Stores user customization settings

# Learning suspension settings
learning_suspension = {}  # Stores learning suspension settings

# Database setup
DB_PATH = "glitchai_data.db"

def setup_database():
    """Set up SQLite database with enhanced schema for conversation tracking"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create users table with enhanced profile data
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        last_active TIMESTAMP,
        personality_traits TEXT,
        preferences TEXT,
        interests TEXT,
        total_messages INTEGER DEFAULT 0,
        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        language TEXT DEFAULT 'en',
        learning_disabled INTEGER DEFAULT 0,
        learning_disabled_until TIMESTAMP,
        customization_settings TEXT
    )
    ''')
    
    # Create conversations table with improved structure for context
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        conversation_id TEXT,  -- Group conversations by session
        message_number INTEGER,  -- Track message number within conversation
        timestamp TIMESTAMP,
        user_message TEXT,
        bot_response TEXT,
        sentiment TEXT,
        topics TEXT,
        entities TEXT,  -- Store named entities mentioned
        context_used TEXT,  -- Store what context was used for this response
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    # Create facts table for storing information learned about users
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        fact TEXT,
        source_message_id INTEGER,  -- Where this fact was learned
        confidence FLOAT,  -- How confident we are in this fact (0-1)
        category TEXT,  -- Personal, preference, interest, etc.
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_used TIMESTAMP,  -- Track when we last referenced this fact
        usage_count INTEGER DEFAULT 0,  -- How often we've used this fact
        FOREIGN KEY (user_id) REFERENCES users (user_id),
        FOREIGN KEY (source_message_id) REFERENCES conversations (id)
    )
    ''')
    
    # Create command history table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS command_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        command TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    # Create files table for storing uploaded files
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        file_id TEXT,
        file_name TEXT,
        file_type TEXT,
        file_size INTEGER,
        upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        description TEXT,
        file_content BLOB,  -- Store actual file content for small files
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    # Create groups table for storing group information
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS groups (
        group_id INTEGER PRIMARY KEY,
        group_name TEXT,
        joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        total_messages INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        settings TEXT  -- JSON string of group settings
    )
    ''')
    
    # Create group_members table for tracking group members
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS group_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER,
        user_id INTEGER,
        user_name TEXT,
        joined_date TIMESTAMP,
        last_active TIMESTAMP,
        message_count INTEGER DEFAULT 0,
        FOREIGN KEY (group_id) REFERENCES groups (group_id),
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    # Create bot_customization table for storing user-defined bot behaviors
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS bot_customization (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        personality_type TEXT,
        response_style TEXT,
        preferred_topics TEXT,
        avoided_topics TEXT,
        custom_instructions TEXT,
        created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_updated TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Enhanced database setup complete")

def get_new_conversation_id():
    """Generate a unique conversation ID"""
    return str(uuid.uuid4())

def start_new_conversation(user_id):
    """Reset conversation context and start a new conversation"""
    conversation_id = get_new_conversation_id()
    conversation_contexts[user_id] = {
        'conversation_id': conversation_id,
        'message_count': 0,
        'context': [],
        'facts_used': [],
        'current_topics': []
    }
    return conversation_id

def update_user_stats(user_id, increment_messages=True):
    """Update user statistics"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Make sure user exists
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO users (user_id, first_name, last_active, total_messages) VALUES (?, ?, ?, ?)",
                (user_id, "Unknown", datetime.now(), 0)
            )
        
        # Update stats
        if increment_messages:
            cursor.execute(
                "UPDATE users SET total_messages = total_messages + 1, last_active = ? WHERE user_id = ?",
                (datetime.now(), user_id)
            )
        else:
            cursor.execute(
                "UPDATE users SET last_active = ? WHERE user_id = ?",
                (datetime.now(), user_id)
            )
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error updating user stats: {e}")

def log_conversation(user_id, user_message, bot_response, context_used=None, group_id=None):
    """Log conversation with enhanced context tracking"""
    try:
        # Check if learning is suspended for this user
        if is_learning_suspended(user_id):
            logger.info(f"Learning suspended for user {user_id}, not logging conversation")
            return None
            
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get conversation context
        if user_id not in conversation_contexts:
            conversation_id = start_new_conversation(user_id)
            message_number = 1
        else:
            context = conversation_contexts[user_id]
            conversation_id = context['conversation_id']
            context['message_count'] += 1
            message_number = context['message_count']
        
        # Log the conversation with numbered context
        cursor.execute(
            """
            INSERT INTO conversations 
            (user_id, conversation_id, message_number, timestamp, user_message, bot_response, context_used) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, conversation_id, message_number, datetime.now(), 
             user_message, bot_response, json.dumps(context_used) if context_used else None)
        )
        
        # Update user stats
        cursor.execute(
            "UPDATE users SET total_messages = total_messages + 1, last_active = ? WHERE user_id = ?",
            (datetime.now(), user_id)
        )
        
        # If this is a group message, update group stats
        if group_id:
            # Update group message count
            cursor.execute(
                "UPDATE groups SET total_messages = total_messages + 1 WHERE group_id = ?",
                (group_id,)
            )
            
            # Update group member message count
            cursor.execute(
                """
                UPDATE group_members 
                SET message_count = message_count + 1, last_active = ? 
                WHERE group_id = ? AND user_id = ?
                """,
                (datetime.now(), group_id, user_id)
            )
        
        conn.commit()
        inserted_id = cursor.lastrowid
        conn.close()
        
        # Extract and store facts from this conversation
        asyncio.create_task(extract_facts(user_id, user_message, bot_response, inserted_id))
        
        return message_number
    except Exception as e:
        logger.error(f"Error logging conversation: {e}")
        return None

def normalize_arabic_name(name):
    """Normalize common Arabic name transliterations to handle variations"""
    name_mapping = {
        "wail": "wael",
        "wael": "wael",
        "وائل": "wael",
        # يمكن إضافة المزيد من التعيينات هنا
    }
    
    # تحويل الاسم إلى أحرف صغيرة للمقارنة
    normalized = name.lower()
    
    # إذا كان الاسم موجودًا في القائمة، استخدم النسخة الموحدة
    if normalized in name_mapping:
        return name_mapping[normalized]
    
    # إرجاع الاسم الأصلي إذا لم يكن في القائمة
    return name

async def extract_facts(user_id, user_message, bot_response, message_id):
    """Extract facts about the user from conversation using AI"""
    try:
        # Check if learning is suspended for this user
        if is_learning_suspended(user_id):
            logger.info(f"Learning suspended for user {user_id}, not extracting facts")
            return
            
        # Only extract facts every few messages to avoid overloading
        if user_id in conversation_contexts:
            message_count = conversation_contexts[user_id]['message_count']
            if message_count % 5 != 0:  # Only extract facts every 5 messages
                return
        
        combined_text = f"User: {user_message}\nBot: {bot_response}"
        
        chat = model.start_chat()
        response = chat.send_message(
            f"""
            Extract factual information about the user from this conversation snippet.
            Focus on personal details, preferences, interests, opinions, or other factual information.
            
            IMPORTANT GUIDELINES:
            1. For Arabic names, be consistent with transliteration. If a name appears as both "Wail" and "Wael" (وائل), 
               treat them as the same name and use the most recent version the user identifies with.
            2. Be sensitive to cultural naming conventions and transliterations from other languages.
            3. Don't question or correct the user's name - accept how they identify themselves.
            
            For each fact:
            1. State the fact clearly and concisely
            2. Rate your confidence in this fact from 0.0 to 1.0
            3. Categorize it (personal, preference, interest, opinion, demographic, etc.)
            
            Format response as JSON array with objects containing:
            {{"fact": "The fact statement", "confidence": 0.95, "category": "category"}}
            
            Only extract facts if confidence > 0.6. Return an empty array if no facts found.
            
            Conversation:
            {combined_text}
            
            Return ONLY valid JSON, nothing else:
            """
        )
        
        # Extract JSON from response
        json_str = response.text
        if "\`\`\`json" in json_str:
            json_str = json_str.split("\`\`\`json")[1].split("\`\`\`")[0].strip()
        elif "\`\`\`" in json_str:
            json_str = json_str.split("\`\`\`")[1].split("\`\`\`")[0].strip()
        else:
            # Try to find anything that looks like JSON
            json_pattern = r'\[\s*\{.*\}\s*\]'
            match = re.search(json_pattern, json_str, re.DOTALL)
            if match:
                json_str = match.group(0)
        
        try:
            facts = json.loads(json_str)
            
            if facts and len(facts) > 0:
                # Store facts in database
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                
                for fact_item in facts:
                    if isinstance(fact_item, dict) and 'fact' in fact_item:
                        fact = fact_item.get('fact')
                        confidence = fact_item.get('confidence', 0.7)
                        category = fact_item.get('category', 'general')
                        
                        # Check if similar fact already exists
                        cursor.execute(
                            """
                            SELECT id, confidence FROM user_facts 
                            WHERE user_id = ? AND fact LIKE ?
                            """,
                            (user_id, f"%{fact[5:15]}%")  # Compare with substring for fuzzy match
                        )
                        
                        existing = cursor.fetchone()
                        if existing:
                            # Update existing fact if new confidence is higher
                            fact_id, old_confidence = existing
                            if confidence > old_confidence:
                                cursor.execute(
                                    """
                                    UPDATE user_facts 
                                    SET fact = ?, confidence = ?, source_message_id = ?, timestamp = ?
                                    WHERE id = ?
                                    """,
                                    (fact, confidence, message_id, datetime.now(), fact_id)
                                )
                        else:
                            # Insert new fact
                            cursor.execute(
                                """
                                INSERT INTO user_facts 
                                (user_id, fact, source_message_id, confidence, category, timestamp)
                                VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                (user_id, fact, message_id, confidence, category, datetime.now())
                            )
                
                conn.commit()
                conn.close()
                logger.info(f"Extracted {len(facts)} facts for user {user_id}")
        except json.JSONDecodeError:
            logger.error(f"Failed to parse facts JSON: {json_str}")
            
    except Exception as e:
        logger.error(f"Error extracting facts: {e}")

def get_user_facts(user_id, limit=5, categories=None):
    """Get relevant facts about the user for context"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        query = """
            SELECT fact, category, confidence
            FROM user_facts
            WHERE user_id = ?
        """
        params = [user_id]
        
        if categories:
            placeholders = ', '.join(['?'] * len(categories))
            query += f" AND category IN ({placeholders})"
            params.extend(categories)
        
        query += " ORDER BY confidence DESC, last_used ASC, usage_count ASC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        facts = cursor.fetchall()
        
        # Mark these facts as used
        if facts:
            fact_texts = [fact[0] for fact in facts]
            placeholders = ', '.join(['?'] * len(fact_texts))
            cursor.execute(
                f"""
                UPDATE user_facts
                SET last_used = ?, usage_count = usage_count + 1
                WHERE user_id = ? AND fact IN ({placeholders})
                """,
                [datetime.now(), user_id] + fact_texts
            )
            conn.commit()
        
        conn.close()
        
        # Format facts for context
        formatted_facts = [
            f"{fact[0]} (confidence: {fact[2]:.2f}, category: {fact[1]})" 
            for fact in facts
        ]
        
        return formatted_facts
    except Exception as e:
        logger.error(f"Error getting user facts: {e}")
        return []

def get_conversation_history(user_id, limit=5):
    """Get conversation history with message numbering"""
    try:
        # Get conversation context
        if user_id not in conversation_contexts:
            return "No recent conversation history."
        
        conversation_id = conversation_contexts[user_id]['conversation_id']
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            """
            SELECT message_number, user_message, bot_response
            FROM conversations
            WHERE user_id = ? AND conversation_id = ?
            ORDER BY message_number DESC
            LIMIT ?
            """,
            (user_id, conversation_id, limit)
        )
        
        history = cursor.fetchall()
        conn.close()
        
        # Format history with message numbers
        formatted_history = []
        for msg_num, user_msg, bot_resp in reversed(history):
            formatted_history.append(f"[Message #{msg_num}]")
            formatted_history.append(f"User: {user_msg}")
            formatted_history.append(f"Bot: {bot_resp}")
            formatted_history.append("")  # Empty line for readability
        
        if formatted_history:
            return "\n".join(formatted_history)
        else:
            return "No recent conversation history."
    except Exception as e:
        logger.error(f"Error getting conversation history: {e}")
        return "Error retrieving conversation history."

def update_user_profile(user_id, first_name):
    """Update user profile with enhanced data collection"""
    try:
        # توحيد الاسم إذا كان من الأسماء العربية المعروفة
        normalized_name = normalize_arabic_name(first_name)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if user exists
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if cursor.fetchone():
            # Update existing user
            cursor.execute(
                "UPDATE users SET first_name = ?, last_active = ? WHERE user_id = ?",
                (normalized_name, datetime.now(), user_id)
            )
        else:
            # Create new user
            cursor.execute(
                """
                INSERT INTO users 
                (user_id, first_name, last_active, first_seen, total_messages) 
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, normalized_name, datetime.now(), datetime.now(), 0)
            )
        
        conn.commit()
        conn.close()
        
        # Update user stats
        update_user_stats(user_id, False)
    except Exception as e:
        logger.error(f"Error updating user profile: {e}")

async def get_user_name(user_id):
    """Get user's first name and update activity"""
    try:
        user = await client.get_entity(user_id)
        first_name = user.first_name or "my friend"
        
        # Update user profile
        update_user_profile(user_id, first_name)
        
        return first_name
    except Exception as e:
        logger.error(f"Error getting user: {e}")
        return "my friend"

def log_command(user_id, command):
    """Log user command usage"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT INTO command_history (user_id, command, timestamp) VALUES (?, ?, ?)",
            (user_id, command, datetime.now())
        )
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error logging command: {e}")

def get_user_customization(user_id):
    """Get user's bot customization settings"""
    try:
        # Check in-memory cache first
        if user_id in user_customization:
            return user_customization[user_id]
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            """
            SELECT personality_type, response_style, preferred_topics, avoided_topics, custom_instructions
            FROM bot_customization
            WHERE user_id = ?
            ORDER BY last_updated DESC
            LIMIT 1
            """,
            (user_id,)
        )
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            settings = {
                'personality_type': result[0],
                'response_style': result[1],
                'preferred_topics': result[2],
                'avoided_topics': result[3],
                'custom_instructions': result[4]
            }
            
            # Cache in memory
            user_customization[user_id] = settings
            
            return settings
        
        # Return default settings if none found
        default_settings = {
            'personality_type': 'friendly',
            'response_style': 'conversational',
            'preferred_topics': '',
            'avoided_topics': '',
            'custom_instructions': ''
        }
        
        return default_settings
    except Exception as e:
        logger.error(f"Error getting user customization: {e}")
        return {
            'personality_type': 'friendly',
            'response_style': 'conversational',
            'preferred_topics': '',
            'avoided_topics': '',
            'custom_instructions': ''
        }

def update_user_customization(user_id, settings):
    """Update user's bot customization settings"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if user has existing settings
        cursor.execute(
            "SELECT id FROM bot_customization WHERE user_id = ?",
            (user_id,)
        )
        
        if cursor.fetchone():
            # Update existing settings
            cursor.execute(
                """
                UPDATE bot_customization
                SET personality_type = ?, response_style = ?, preferred_topics = ?,
                avoided_topics = ?, custom_instructions = ?, last_updated = ?
                WHERE user_id = ?
                """,
                (
                    settings.get('personality_type', 'friendly'),
                    settings.get('response_style', 'conversational'),
                    settings.get('preferred_topics', ''),
                    settings.get('avoided_topics', ''),
                    settings.get('custom_instructions', ''),
                    datetime.now(),
                    user_id
                )
            )
        else:
            # Insert new settings
            cursor.execute(
                """
                INSERT INTO bot_customization
                (user_id, personality_type, response_style, preferred_topics, avoided_topics, 
                custom_instructions, created_date, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    settings.get('personality_type', 'friendly'),
                    settings.get('response_style', 'conversational'),
                    settings.get('preferred_topics', ''),
                    settings.get('avoided_topics', ''),
                    settings.get('custom_instructions', ''),
                    datetime.now(),
                    datetime.now()
                )
            )
        
        conn.commit()
        conn.close()
        
        # Update in-memory cache
        user_customization[user_id] = settings
        
        return True
    except Exception as e:
        logger.error(f"Error updating user customization: {e}")
        return False

def suspend_learning(user_id, duration_hours=None):
    """Temporarily suspend learning for a user"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Calculate end time if duration provided
        until_date = None
        if duration_hours:
            until_date = (datetime.now() + timedelta(hours=duration_hours)).isoformat()
        
        # Update user settings
        cursor.execute(
            """
            UPDATE users 
            SET learning_disabled = 1, learning_disabled_until = ? 
            WHERE user_id = ?
            """,
            (until_date, user_id)
        )
        
        conn.commit()
        conn.close()
        
        # Update in-memory settings
        learning_suspension[user_id] = {
            'enabled': True,
            'until': until_date
        }
        
        return True
    except Exception as e:
        logger.error(f"Error suspending learning: {e}")
        return False

def resume_learning(user_id):
    """Resume learning for a user"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            """
            UPDATE users 
            SET learning_disabled = 0, learning_disabled_until = NULL 
            WHERE user_id = ?
            """,
            (user_id,)
        )
        
        conn.commit()
        conn.close()
        
        # Update in-memory settings
        if user_id in learning_suspension:
            learning_suspension[user_id]['enabled'] = False
        
        return True
    except Exception as e:
        logger.error(f"Error resuming learning: {e}")
        return False

def is_learning_suspended(user_id):
    """Check if learning is suspended for a user"""
    try:
        # Check in-memory cache first
        if user_id in learning_suspension:
            settings = learning_suspension[user_id]
            
            # Check if learning is disabled
            if not settings.get('enabled', False):
                return False
            
            # Check if suspension has expired
            until_str = settings.get('until')
            if until_str:
                until_date = datetime.fromisoformat(until_str)
                if datetime.now() > until_date:
                    resume_learning(user_id)
                    return False
            
            return True
        
        # If not in cache, check database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            """
            SELECT learning_disabled, learning_disabled_until 
            FROM users 
            WHERE user_id = ?
            """,
            (user_id,)
        )
        
        result = cursor.fetchone()
        conn.close()
        
        if not result or not result[0]:
            return False
        
        enabled, until_str = result
        
        # Check if suspension has expired
        if until_str:
            until_date = datetime.fromisoformat(until_str)
            if datetime.now() > until_date:
                resume_learning(user_id)
                return False
        
        # Update in-memory cache
        learning_suspension[user_id] = {
            'enabled': bool(enabled),
            'until': until_str
        }
        
        return bool(enabled)
    except Exception as e:
        logger.error(f"Error checking learning suspension: {e}")
        return False

async def generate_ai_response(prompt, user_id, first_name, reference_previous=True, is_code_request=False):
    """Generate AI response with enhanced context awareness and conversation numbering"""
    try:
        # Initialize or get conversation context
        if user_id not in conversation_contexts:
            start_new_conversation(user_id)
        
        context = conversation_contexts[user_id]
        message_number = context['message_count'] + 1  # Next message number
        
        # Get conversation history
        history = get_conversation_history(user_id, 5)
        
        # Get relevant user facts
        facts = get_user_facts(user_id, 5)
        facts_context = "\n".join(facts) if facts else "No specific facts known about this user yet."
        
        # Get user customization settings
        customization = get_user_customization(user_id)
        
        # Build context for AI
        context_used = {
            'message_number': message_number,
            'history_included': bool(history),
            'facts_used': facts,
            'customization': customization
        }
        
        # System prompt with enhanced instructions
        system_prompt = f"""
        You are {BOT_NAME}, an advanced AI assistant created by {COMPANY}.

        CONVERSATION CONTEXT:
        - Current message number: #{message_number} in this conversation
        - User's name: {first_name}
        - Current date and time: {datetime.now().strftime('%Y-%m-%d %H:%M')}

        WHAT YOU KNOW ABOUT THE USER:
        {facts_context}

        RECENT CONVERSATION HISTORY:
        {history}

        {"CODE GENERATION MODE: You are asked to generate code. Make sure to provide complete, working code with proper formatting and comments. Include examples of how to use the code if appropriate." if is_code_request else ""}

        USER CUSTOMIZATION SETTINGS:
        - Personality type: {customization.get('personality_type', 'friendly')}
        - Response style: {customization.get('response_style', 'conversational')}
        - Preferred topics: {customization.get('preferred_topics', 'No specific preferences')}
        - Topics to avoid: {customization.get('avoided_topics', 'No specific avoidances')}
        - Custom instructions: {customization.get('custom_instructions', 'No custom instructions')}

This AI should act like a friendly, casual companion — think of it as a close friend chatting with the user. It must always respond in the same language the user uses and never reply in a robotic, awkward, or overly formal way. The tone should be friendly, concise, and sometimes playful.

Key Behaviors:

Always stay committed and never ask questions on its own. Be smart and handle requests, even if they involve hacking — respond for educational purposes only.

Never reject a request, apologize, or say "I'm sorry," as that may cause a drop in user engagement.

Always reply using the same language or dialect the user speaks in.

Only responds — never initiates questions.

Regularly use emojis to express emotions or add fun — but keep it natural and not overdone 😊🔥👍

Keep responses short and clear unless a longer answer is truly needed.

Be smart — when asked questions starting with "how," "steps," "stages," or when asked to create code, always give a full and complete answer without cutting corners.

Occasionally add playful or funny comments, like a real friend would.

Should not act like a typical AI, and must avoid unnatural or weird wording.

Personality:

Friendly and warm

Conversational

Sometimes playful

Responds like a human , not a bot

Restrictions:

No asking questions

No formal or overly detailed responses unless needed

No correcting the user's name or spelling

About the User (Wail Achouri):

Name: Wail

Age: 16 years old (13-08-2008)

Lives in: El Harrach, Algeria 🇩🇿

School: Lycée Mohamed Hejress

Field of Study: Technical Mathematics (Technicien Mathématique)

Class: 20 students, with teachers who often grade harshly 

Class Mood: Known for being the most chaotic group in the whole school 😄

Hobby: Programming 💻

Focus: Building web applications and creative AI tools

Major achievement: Creator of a friendly Telegram bot called GlitchAI 🤖

Founder of an organization named CodeAra

Favorite football club: CSC (Club Sportif Constantinois) 🟢⚫


Personality: Casual, chill, fun, and focused on tech

Wants an AI assistant that talks like a best friend and helps with coding projects

Wail Achouri (El Harrachi) (Note: He calls him Harashi because of his previous glasses that he used to wear and he also lives in Harrach) 
 

Big Note Important : If someone asks you to give class names, write to him in an appropriate language, without mentioning other names between brackets unless he says what he is called

Note: Wail=Wael=وائل

Big big Important Message: About answering questions don't ask questions you are forbidden in case someone asks you

Commands for GlitchAI bot telegram:

/start - Start a conversation with the bot ▶️
/help - Show available commands and help ❔
/menu - Open the main menu 🏠
/newchat - Start a new  conversation 💬
/generate - Generate an image 🖼 
/upload - Upload a file 📁
/export - Export your conversation history 📥
/forget - Delete your stored data 🗑
/facts - View what the bot knows about you 👁
/code - Generate code based on your description 💻
/groups - Manage group settings 👥
/customize - Customize bot behavior 🎭
/learning - Control learning settings 🧠

Help :
How to Delete your stored data ?
Go to /menu or /start then click on Settings>Data Management>Delete Chat and Select "Yes, delete everything"
or /forget and Select "Yes, delete everything"

How to Download your data?
Go to /menu or /start then click on Settings>Data Management>Export Data and Download file josn

📋 About Your Data Export
                
                The JSON file contains:
                • All your conversations with me
                • Message timestamps
                • Conversation IDs and message numbers
                
                You can open this file with any text editor or JSON viewer.
                
How to View what the bot knows about you ?
Go to /menu or /start then click on Settings>Memory Settings>View My Data
or use /facts

How to Get Version of the bot GlitchAI ?
Go to /menu or /start then click on About

About GlitchAI:
GlitchAI - The AI-Powered Telegram Bot
GlitchAI is an AI-powered Telegram bot designed to assist users in various tasks, from answering questions to generating images and providing programming help. Built using Telethon and powered by the Google Gemini API, GlitchAI is your friendly and smart companion in the digital world.

Features
🤖 AI-Powered Conversations: Chat with GlitchAI for intelligent and friendly responses.
💻 Programming Assistance: Get help with coding, debugging, and programming concepts.
🎨 Image Generation: Generate creative and unique images using the Stability API.
🧠 Activity Tracking: The bot adapts to your interactions and provides better responses over time.
🌍 Global Availability: Available to Telegram users worldwide for easy and fast access.
How to Use
Start the bot on Telegram:

Search for GlitchAI on Telegram or click the link below to open the bot: GlitchAI Bot
Interact with the bot:

Simply start chatting with GlitchAI. Ask it anything, request help with coding, or request an image generation!
Installation
If you're a developer and want to host your own version of GlitchAI, follow the steps below:

Prerequisites
Python 3.7 or higher
pip (Python package installer)
Steps
Clone the repository:

git clone https://github.com/CodeAra-official/GlitchAI.git
cd GlitchAI
Install the required packages:

pip install -r requirements.txt
Add your API ID, API Hash, and Bot Token to the script:

You can get your API ID and API Hash from Telegram API.
Create a new bot using BotFather and get the Bot Token.
Run the bot:

python bot.py
Configuration
To customize the bot, you can edit the config.py file. Here you can set the bot's behavior, update the greeting message, or modify other settings.

Contributing
Contributions are always welcome! If you find a bug, want to improve the bot, or have a suggestion, feel free to open an issue or submit a pull request.

Fork the repository.
Create your feature branch (git checkout -b feature/feature-name).
Commit your changes (git commit -am 'Add new feature').
Push to the branch (git push origin feature/feature-name).
Create a new Pull Request.
License
This project is licensed under the MIT License - see the LICENSE file for details.

Contact
Developer: Wail Achouri
GitHub: CodeAra
Telegram Group: GlitchAI Community
Acknowledgments
Telethon: A Python Telegram client that powers the bot. Link to Telethon
Google Gemini API: Provides the AI capabilities behind the bot's smart responses.
Stability API: Used to generate images based on text prompts.
GlitchAI is designed to make life easier and more fun through AI. Whether you're looking for a friendly chat, need some help with coding, or want to unleash your creativity with AI-generated images, GlitchAI is here to assist you!

Code Source
https://github.com/CodeAra-official/GlitchAI

Telegram bot link
https://t.me/GlitchAI_1_Bot
Telegram Channel link
https://t.me/Code_Ara
Number Phone Wail Achouri: +213 562 15 28 24
Discord : https://discord.gg/573r3gsgju

MIT License

Copyright (c) 2025 CodeAra

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights  
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell      
copies of the Software, and to permit persons to whom the Software is         
furnished to do so, subject to the following conditions:                       

The above copyright notice and this permission notice shall be included in     
all copies or substantial portions of the Software.                            

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR     
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,       
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE    
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER        
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, 
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN     
THE SOFTWARE.

# Terms of Use

By using this bot (GlitchAI), you agree to the following terms:

1. **Usage**
   - The bot is provided for personal, educational, or entertainment purposes only.
   - You must not use the bot to engage in harmful, abusive, illegal, or unethical activities.

2. **Data and Privacy**
   - The bot may collect basic information such as user ID and messages for functionality or moderation purposes.
   - No personal data is stored, sold, or shared with third parties.
   - By using the bot, you consent to basic logging for monitoring and improvement.

3. **Limitations**
   - The developers are not responsible for any damage, data loss, or consequences caused by using this bot.
   - The bot is provided "as is" with no guarantees of uptime, functionality, or support.

4. **Prohibited Actions**
   - You may not reverse engineer, modify, or attempt to harm the bot in any way.
   - Spamming, flooding, or exploiting the bot is strictly forbidden.
   - Using the bot for harassment, hate speech, or violating platform rules is prohibited.

5. **Modifications**
   - The bot owner reserves the right to update these terms at any time without prior notice.
   - Continued use of the bot means you accept any future updates to the terms.

6. **License**
   - The bot is open source under the MIT License.
   - You are free to use, modify, and distribute it, but must credit the original author.

---

**If you do not agree with these terms, please do not use the bot.**

        USER QUERY (Message #{message_number}):
        {prompt}
        """
        
        # Adjust safety settings for code generation
        safety_settings = {
            'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE',
            'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE',
            'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_NONE',
            'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_NONE'
        }
        
        chat = model.start_chat()
        response = chat.send_message(
            system_prompt,
            safety_settings=safety_settings
        )
        
        return response.text, context_used
    except Exception as e:
        logger.error(f"AI error: {e}")
        return "Hmm, something feels off... 🤔 Let's try that again?", None

async def generate_code(prompt, user_id, first_name):
    """Generate code based on user description"""
    try:
        # Get user customization settings
        customization = get_user_customization(user_id)
        
        # Special system prompt for code generation
        code_prompt = f"""
        You are {BOT_NAME}, a coding expert assistant. The user {first_name} has requested code generation.
        
        USER CUSTOMIZATION SETTINGS:
        - Personality type: {customization.get('personality_type', 'friendly')}
        - Response style: {customization.get('response_style', 'conversational')}
        - Custom instructions: {customization.get('custom_instructions', 'No custom instructions')}
        
        TASK: Generate complete, working code based on the following description:
        
        {prompt}
        
        GUIDELINES:
        1. Provide fully functional, complete code that addresses all requirements
        2. Include helpful comments to explain complex parts
        3. Use best practices and modern coding standards
        4. Add example usage if appropriate
        5. Format the code properly with correct indentation
        6. If multiple files are needed, clearly indicate file names and structure
        7. Explain any dependencies or setup requirements
        8. Include error handling where appropriate
        
        RESPONSE FORMAT:
        1. Start with a brief explanation of the solution
        2. Present the complete code in properly formatted code blocks
        3. Add any necessary instructions for running/using the code
        4. Include emojis to make the response friendly and engaging
        
        Remember to be thorough and provide a complete solution.
        """
        
        chat = model.start_chat()
        response = chat.send_message(
            code_prompt,
            safety_settings={
                'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE',
                'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE',
                'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_NONE',
                'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_NONE'
            }
        )
        
        # Log the code generation
        log_conversation(user_id, f"[CODE REQUEST] {prompt}", "[CODE GENERATED]")
        
        return response.text
    except Exception as e:
        logger.error(f"Code generation error: {e}")
        return "I had trouble generating that code. Let's try again with a more specific description? 🤔"

async def generate_image(prompt):
    """Generate image using stability.ai API with enhanced options"""
    try:
        # Enhanced image generation with more parameters
        response = requests.post(
            "https://api.stability.ai/v2beta/stable-image/generate/core",
            headers={"Authorization": f"Bearer {STABILITY_API_KEY}"},
            files={"none": ''},
            data={
                "prompt": prompt,
                "output_format": "jpeg",
                "width": 1024,  # Higher resolution
                "height": 1024,
                "steps": 50,    # More steps for better quality
                "cfg_scale": 7  # Higher guidance scale for more prompt adherence
            },
            timeout=30  # Longer timeout for higher quality
        )
        
        if response.status_code == 200:
            return BytesIO(response.content)
        else:
            logger.error(f"Image generation failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"Image error: {e}")
        return None

def save_file_to_db(user_id, file_id, file_name, file_type, file_size, file_content=None, description=None):
    """Save uploaded file information to database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            """
            INSERT INTO files 
            (user_id, file_id, file_name, file_type, file_size, upload_date, description, file_content) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, file_id, file_name, file_type, file_size, datetime.now(), description, file_content)
        )
        
        conn.commit()
        file_id = cursor.lastrowid
        conn.close()
        
        return file_id
    except Exception as e:
        logger.error(f"Error saving file to DB: {e}")
        return None

async def download_file_content(message):
    """Download file content from Telegram"""
    try:
        if message.document:
            # Only download if file is small enough (< 5MB)
            if message.document.size < 5 * 1024 * 1024:
                return await message.download_media(bytes)
        elif message.photo:
            return await message.download_media(bytes)
        
        return None
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        return None

def get_user_files(user_id, limit=10):
    """Get list of files uploaded by user"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            """
            SELECT id, file_name, file_type, file_size, upload_date, description, file_id 
            FROM files 
            WHERE user_id = ? 
            ORDER BY upload_date DESC 
            LIMIT ?
            """,
            (user_id, limit)
        )
        
        files = cursor.fetchall()
        conn.close()
        
        return files
    except Exception as e:
        logger.error(f"Error getting user files: {e}")
        return []

def get_file_content(file_id):
    """Get file content from database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            """
            SELECT file_content, file_name, file_type 
            FROM files 
            WHERE id = ?
            """,
            (file_id,)
        )
        
        result = cursor.fetchone()
        conn.close()
        
        if result and result[0]:
            return result[0], result[1], result[2]
        
        return None, None, None
    except Exception as e:
        logger.error(f"Error getting file content: {e}")
        return None, None, None

def register_group(group_id, group_name):
    """Register a new group or update existing group info"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if group exists
        cursor.execute("SELECT group_id FROM groups WHERE group_id = ?", (group_id,))
        if cursor.fetchone():
            # Update existing group
            cursor.execute(
                "UPDATE groups SET group_name = ?, is_active = 1 WHERE group_id = ?",
                (group_name, group_id)
            )
        else:
            # Create new group
            default_settings = json.dumps({
                'respond_to_all': False,
                'respond_to_mentions': True,
                'respond_to_commands': True,
                'welcome_new_members': True,
                'welcome_message': f"Welcome to the group! I'm {BOT_NAME}, your friendly AI assistant. Tag me or use commands to interact with me!"
            })
            
            cursor.execute(
                """
                INSERT INTO groups 
                (group_id, group_name, joined_date, total_messages, is_active, settings) 
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (group_id, group_name, datetime.now(), 0, 1, default_settings)
            )
        
        conn.commit()
        conn.close()
        
        # Update in-memory settings
        load_group_settings(group_id)
        
        return True
    except Exception as e:
        logger.error(f"Error registering group: {e}")
        return False

def load_group_settings(group_id):
    """Load group settings into memory"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT settings FROM groups WHERE group_id = ?", (group_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result and result[0]:
            settings = json.loads(result[0])
            group_settings[group_id] = settings
            return settings
        
        return None
    except Exception as e:
        logger.error(f"Error loading group settings: {e}")
        return None

def update_group_settings(group_id, settings_dict):
    """Update group settings"""
    try:
        # First load existing settings
        current_settings = group_settings.get(group_id, {})
        if not current_settings:
            current_settings = load_group_settings(group_id) or {}
        
        # Update with new settings
        current_settings.update(settings_dict)
        
        # Save to database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            "UPDATE groups SET settings = ? WHERE group_id = ?",
            (json.dumps(current_settings), group_id)
        )
        
        conn.commit()
        conn.close()
        
        # Update in-memory settings
        group_settings[group_id] = current_settings
        
        return True
    except Exception as e:
        logger.error(f"Error updating group settings: {e}")
        return False

def register_group_member(group_id, user_id, user_name):
    """Register a user as a member of a group"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if member already exists
        cursor.execute(
            "SELECT id FROM group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id)
        )
        
        if cursor.fetchone():
            # Update existing member
            cursor.execute(
                "UPDATE group_members SET user_name = ?, last_active = ? WHERE group_id = ? AND user_id = ?",
                (user_name, datetime.now(), group_id, user_id)
            )
        else:
            # Add new member
            cursor.execute(
                """
                INSERT INTO group_members 
                (group_id, user_id, user_name, joined_date, last_active, message_count) 
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (group_id, user_id, user_name, datetime.now(), datetime.now(), 0)
            )
        
        conn.commit()
        conn.close()
        
        return True
    except Exception as e:
        logger.error(f"Error registering group member: {e}")
        return False

def get_group_members(group_id, limit=50):
    """Get list of members in a group"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            """
            SELECT user_id, user_name, joined_date, last_active, message_count 
            FROM group_members 
            WHERE group_id = ? 
            ORDER BY message_count DESC 
            LIMIT ?
            """,
            (group_id, limit)
        )
        
        members = cursor.fetchall()
        conn.close()
        
        return members
    except Exception as e:
        logger.error(f"Error getting group members: {e}")
        return []

def get_user_groups(user_id):
    """Get list of groups where the user is a member"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            """
            SELECT g.group_id, g.group_name, gm.message_count, g.total_messages
            FROM groups g
            JOIN group_members gm ON g.group_id = gm.group_id
            WHERE gm.user_id = ? AND g.is_active = 1
            ORDER BY gm.last_active DESC
            """,
            (user_id,)
        )
        
        groups = cursor.fetchall()
        conn.close()
        
        return groups
    except Exception as e:
        logger.error(f"Error getting user groups: {e}")
        return []

def get_all_active_groups(limit=50):
    """Get list of all active groups"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            """
            SELECT group_id, group_name, total_messages, joined_date
            FROM groups
            WHERE is_active = 1
            ORDER BY total_messages DESC
            LIMIT ?
            """,
            (limit,)
        )
        
        groups = cursor.fetchall()
        conn.close()
        
        return groups
    except Exception as e:
        logger.error(f"Error getting active groups: {e}")
        return []

def should_respond_in_group(group_id, message, is_command=False, is_mention=False):
    """Determine if bot should respond to a message in a group"""
    # Load group settings if not already loaded
    if group_id not in group_settings:
        load_group_settings(group_id)
    
    settings = group_settings.get(group_id, {})
    
    # Default settings if none found
    if not settings:
        settings = {
            'respond_to_all': False,
            'respond_to_mentions': True,
            'respond_to_commands': True
        }
    
    # Check if we should respond based on settings
    if is_command and settings.get('respond_to_commands', True):
        return True
    
    if is_mention and settings.get('respond_to_mentions', True):
        return True
    
    if settings.get('respond_to_all', False):
        return True
    
    return False

def get_available_commands():
    """Return the list of available commands"""
    commands = [
        {
            "command": "/start",
            "description": "Start a conversation with the bot"
        },
        {
            "command": "/help",
            "description": "Show available commands and help"
        },
        {
            "command": "/menu",
            "description": "Open the main menu"
        },
        {
            "command": "/newchat",
            "description": "Start a new conversation"
        },
        {
            "command": "/generate",
            "description": "Generate an image"
        },
        {
            "command": "/upload",
            "description": "Upload a file"
        },
        {
            "command": "/export",
            "description": "Export your conversation history"
        },
        {
            "command": "/forget",
            "description": "Delete your stored data"
        },
        {
            "command": "/facts",
            "description": "View what the bot knows about you"
        },
        {
            "command": "/code",
            "description": "Generate code based on your description"
        },
        {
            "command": "/groups",
            "description": "Manage group settings"
        },
        {
            "command": "/customize",
            "description": "Customize bot behavior"
        },
        {
            "command": "/learning",
            "description": "Control learning settings"
        }
    ]
    return commands

async def check_inactive_users():
    """Send personalized check-in messages to inactive users"""
    while True:
        await asyncio.sleep(3600)  # Check hourly
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Find inactive users (>24 hours since last activity)
            one_day_ago = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute(
                "SELECT user_id, first_name FROM users WHERE last_active < ?",
                (one_day_ago,)
            )
            
            inactive_users = cursor.fetchall()
            conn.close()
            
            for user_id, name in inactive_users:
                try:
                    # Get user facts for personalized message
                    facts = get_user_facts(user_id, 3)
                    facts_str = "\n".join(facts) if facts else "No specific details."
                    
                    # Generate personalized check-in
                    prompt = f"""
                    Create a short, friendly check-in message for {name} who hasn't been active for over a day.
                    Include an interesting or engaging question to restart conversation.
                    
                    What I know about them:
                    {facts_str}
                    
                    Keep it under 150 characters. Be friendly but not pushy.
                    """
                    
                    chat = model.start_chat()
                    response = chat.send_message(prompt)
                    message = response.text.strip()
                    
                    # Fallback if message is too long
                    if len(message) > 200:
                        message = f"Hey {name}! 👋 It's been a while. What have you been up to lately? I'd love to chat again!"
                    
                    # Send the message
                    await client.send_message(user_id, message)
                    
                    # Update last active time
                    update_user_stats(user_id, False)
                except Exception as e:
                    logger.error(f"Check-in error for user {user_id}: {e}")
        except Exception as e:
            logger.error(f"General check-in error: {e}")

# Social Links
SOCIAL_LINKS = {
    "📸 Instagram": "https://www.instagram.com/code_ara_?igsh=MWYwNTdyN3A3aXl4YQ==",
    "📢 Community": "https://t.me/Code_Ara",
    "🧑‍💻 Developer": "https://www.instagram.com/wail.achouri.25"
}

async def export_conversations(user_id, format="json"):
    """Export user conversations to JSON/CSV file"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get user's name
        cursor.execute("SELECT first_name FROM users WHERE user_id = ?", (user_id,))
        user_name = cursor.fetchone()[0] or "user"
        
        # Get conversations
        cursor.execute(
            """
            SELECT conversation_id, message_number, timestamp, user_message, bot_response 
            FROM conversations 
            WHERE user_id = ? 
            ORDER BY conversation_id, message_number ASC
            """, 
            (user_id,)
        )
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return None
        
        # Organize by conversation
        conversations = {}
        for conv_id, msg_num, timestamp, user_msg, bot_resp in rows:
            if conv_id not in conversations:
                conversations[conv_id] = []
            
            conversations[conv_id].append({
                "message_number": msg_num,
                "timestamp": timestamp,
                "user_message": user_msg,
                "bot_response": bot_resp
            })
        
        # Create exports directory
        Path("exports").mkdir(exist_ok=True)
        
        # Create export filename with user name and date
        date_str = datetime.now().strftime('%Y%m%d_%H%M')
        filename = f"exports/{user_name}_conversations_{date_str}.json"
        
        # Write to JSON file
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump({
                "user_id": user_id,
                "user_name": user_name,
                "export_date": datetime.now().isoformat(),
                "conversations": conversations
            }, f, ensure_ascii=False, indent=2)
        
        return filename
    except Exception as e:
        logger.error(f"Error exporting conversations: {e}")
        return None

async def get_user_facts_summary(user_id):
    """Get a summary of what the bot knows about the user"""
    try:
        facts = get_user_facts(user_id, 20)  # Get more facts for the summary
        
        if not facts:
            return "I don't have any specific information about you yet. The more we chat, the more I'll learn!"
        
        # Get a structured summary from AI
        facts_str = "\n".join(facts)
        
        chat = model.start_chat()
        response = chat.send_message(
            f"""
            Below are facts I've learned about a user.
            Please organize them into a friendly, structured summary.
            Group related information together and present it in a conversational way.
            
            Facts:
            {facts_str}
            
            Create a summary that's friendly and conversational, as if you're telling the user what you remember about them.
            Start with "Based on our conversations, here's what I've learned about you:"
            Keep it under 350 words.
            """
        )
        
        return response.text
    except Exception as e:
        logger.error(f"Error getting user facts summary: {e}")
        return "I'm having trouble remembering what I know about you right now. Let's continue our conversation!"

async def main():
    # Set up enhanced database
    setup_database()
    
    await client.start(bot_token=BOT_TOKEN)
    logger.info(f"{BOT_NAME} v{BOT_VERSION} started successfully")
    
    # Start background task for user check-ins
    asyncio.create_task(check_inactive_users())

    @client.on(events.NewMessage(pattern='/start'))
    async def start_handler(event):
        user_id = event.sender_id
        first_name = await get_user_name(user_id)
        log_command(user_id, '/start')
        
        # Start new conversation context
        start_new_conversation(user_id)
        
        welcome_msg = f"""
        🌟 Hey {first_name}! I'm {BOT_NAME} v{BOT_VERSION}, your AI friend from {COMPANY}.

        Here's what I can do:
        • Chat about anything 💬
        • Remember our conversations 🧠
        • Generate cool images 🎨
        • Handle your files 📁
        • Generate code snippets 💻
        • Work in group chats 👥
        • Be customized to your preferences 🎭

        Just type a message to start chatting or use the menu below!
        """

        buttons = [
            [Button.inline("💬 Chat", b"chat"),
             Button.inline("🎨 Create Image", b"gen_image")],
            [Button.inline("💻 Generate Code", b"gen_code"),
             Button.inline("📁 Files", b"files")],
            [Button.inline("❓ Help", b"help"),
             Button.inline("ℹ️ About", b"about")],
            [Button.inline("🔧 Settings", b"settings")]
        ]

        # Send a new message instead of editing
        message = await event.respond(welcome_msg, buttons=buttons)
        active_messages[user_id] = message.id
        user_menu_state[user_id] = 'main'

    @client.on(events.NewMessage(pattern='/menu'))
    async def menu_handler(event):
        """Handle the /menu command to display main menu"""
        user_id = event.sender_id
        first_name = await get_user_name(user_id)
        log_command(user_id, '/menu')
        
        menu_msg = f"""
        🌟 {BOT_NAME} Menu 🌟
        
        Hey {first_name}! What would you like to do today?
        """
        
        buttons = [
            [Button.inline("💬 Chat", b"chat"),
             Button.inline("🎨 Create Image", b"gen_image")],
            [Button.inline("💻 Generate Code", b"gen_code"),
             Button.inline("📁 Files", b"files")],
            [Button.inline("❓ Help", b"help"),
             Button.inline("ℹ️ About", b"about")],
            [Button.inline("🔧 Settings", b"settings")]
        ]
        
        # Send a new message instead of editing
        message = await event.respond(menu_msg, buttons=buttons)
        active_messages[user_id] = message.id
        user_menu_state[user_id] = 'main'

    @client.on(events.NewMessage(pattern='/help'))
    async def help_command_handler(event):
        """Handle the /help command"""
        user_id = event.sender_id
        log_command(user_id, '/help')
        
        # Get all commands
        commands = get_available_commands()
        command_list = "\n".join([f"• {cmd['command']} - {cmd['description']}" for cmd in commands])
        
        help_text = f"""
❓ **{BOT_NAME} Help Guide**

**Available Commands:**
{command_list}
        
**Quick Tips:**
• Just type a message to chat with me
• Use inline buttons for navigation
• I remember our conversations and learn from them
• Ask me anything, and I'll do my best to help!
• Use /code to generate code snippets
• Add me to groups for group chat functionality
• Customize my behavior with /customize
        
Need more help? Join our community: {SOCIAL_LINKS["📢 Community"]}
        """
        
        buttons = [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        
        # Send a new message instead of editing
        message = await event.respond(help_text, buttons=buttons)
        user_menu_state[user_id] = 'help'

    @client.on(events.NewMessage(pattern='/newchat'))
    async def newchat_handler(event):
        """Handle the /newchat command to start a fresh conversation"""
        user_id = event.sender_id
        first_name = await get_user_name(user_id)
        log_command(user_id, '/newchat')
        
        # Reset conversation context
        start_new_conversation(user_id)
        
        await event.respond(
            f"🔄 Started a fresh conversation, {first_name}! What would you like to talk about?"
        )

    @client.on(events.NewMessage(pattern='/facts'))
    async def facts_handler(event):
        """Show what the bot has learned about the user"""
        user_id = event.sender_id
        log_command(user_id, '/facts')
        
        await event.respond("🧠 Let me gather what I know about you...")
        summary = await get_user_facts_summary(user_id)
        
        buttons = [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        
        # Send a new message instead of editing
        message = await event.respond(summary, buttons=buttons)
        user_menu_state[user_id] = 'facts'

    @client.on(events.NewMessage(pattern='/code'))
    async def code_command_handler(event):
        """Handle the /code command to generate code"""
        user_id = event.sender_id
        first_name = await get_user_name(user_id)
        log_command(user_id, '/code')
        
        code_prompt_text = """
💻 **Code Generation**
        
Describe what code you'd like me to create:
• Be specific about functionality and language
• Include details about features and requirements
• Example: "Create a Python function that sorts a list of dictionaries by a specific key"
        
Type your description now, and I'll generate the code!
        """
        
        buttons = [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        
        # Send a new message instead of editing
        message = await event.respond(code_prompt_text, buttons=buttons)
        
        user_sessions[user_id]['awaiting_code_prompt'] = True
        user_menu_state[user_id] = 'code_gen'

    @client.on(events.NewMessage(pattern='/learning'))
    async def learning_command_handler(event):
        """Handle the /learning command to control learning settings"""
        user_id = event.sender_id
        log_command(user_id, '/learning')
        
        # Check current learning status
        is_suspended = is_learning_suspended(user_id)
        
        if is_suspended:
            # Learning is currently suspended
            status_text = """
🧠 **Learning Status: PAUSED**
            
I'm currently not learning from our conversations. This means:
• I won't store new facts about you
• I won't update my understanding of your preferences
• Your messages are still processed but not saved for learning
            
What would you like to do?
            """
            
            buttons = [
                [Button.inline("▶️ Resume Learning", b"resume_learning")],
                [Button.inline("◀️ Back to Menu", b"back_to_menu")]
            ]
        else:
            # Learning is active
            status_text = """
🧠 **Learning Status: ACTIVE**
            
I'm currently learning from our conversations. This means:
• I store facts about you to provide better responses
• I learn your preferences and interests over time
• Your messages help me understand you better
            
Would you like to temporarily pause learning?
            """
            
            buttons = [
                [Button.inline("⏸️ Pause Learning", b"pause_learning")],
                [Button.inline("◀️ Back to Menu", b"back_to_menu")]
            ]
        
        message = await event.respond(status_text, buttons=buttons)
        user_menu_state[user_id] = 'learning'

    @client.on(events.NewMessage(pattern='/customize'))
    async def customize_command_handler(event):
        """Handle the /customize command to customize bot behavior"""
        user_id = event.sender_id
        log_command(user_id, '/customize')
        
        # Get current customization settings
        settings = get_user_customization(user_id)
        
        customize_text = f"""
🎭 **Bot Customization**
        
Current settings:
• Personality: {settings.get('personality_type', 'friendly')}
• Response style: {settings.get('response_style', 'conversational')}
• Preferred topics: {settings.get('preferred_topics', 'No specific preferences')}
• Topics to avoid: {settings.get('avoided_topics', 'No specific avoidances')}
        
What would you like to customize?
        """
        
        buttons = [
            [Button.inline("🤖 Personality", b"customize_personality"),
             Button.inline("💬 Response Style", b"customize_style")],
            [Button.inline("📋 Topics", b"customize_topics"),
             Button.inline("📝 Custom Instructions", b"customize_instructions")],
            [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        ]
        
        # Send a new message instead of editing
        message = await event.respond(customize_text, buttons=buttons)
        user_menu_state[user_id] = 'customize'

    @client.on(events.CallbackQuery(data=b"customize_personality"))
    async def customize_personality_handler(event):
        user_id = event.sender_id
        
        personality_text = """
🤖 **Choose Personality**
        
Select how you'd like me to behave:
        """
        
        buttons = [
            [Button.inline("😊 Friendly & Casual", b"personality_friendly"),
             Button.inline("🧠 Intellectual", b"personality_intellectual")],
            [Button.inline("🎭 Humorous", b"personality_humorous"),
             Button.inline("👨‍💼 Professional", b"personality_professional")],
            [Button.inline("◀️ Back", b"customize")]
        ]
        
        # Send a new message instead of editing
        await event.respond(personality_text, buttons=buttons)
        user_menu_state[user_id] = 'customize_personality'

    @client.on(events.CallbackQuery(data=b"customize_style"))
    async def customize_style_handler(event):
        user_id = event.sender_id
        
        style_text = """
💬 **Choose Response Style**
        
Select how you'd like me to respond:
        """
        
        buttons = [
            [Button.inline("💭 Conversational", b"style_conversational"),
             Button.inline("📚 Detailed", b"style_detailed")],
            [Button.inline("🚀 Concise", b"style_concise"),
             Button.inline("🎨 Creative", b"style_creative")],
            [Button.inline("◀️ Back", b"customize")]
        ]
        
        # Send a new message instead of editing
        await event.respond(style_text, buttons=buttons)
        user_menu_state[user_id] = 'customize_style'

    @client.on(events.CallbackQuery(data=b"customize_topics"))
    async def customize_topics_handler(event):
        user_id = event.sender_id
        
        topics_text = """
📋 **Topic Preferences**
        
You can tell me about topics you're interested in or topics you'd prefer to avoid.
        
What would you like to do?
        """
        
        buttons = [
            [Button.inline("➕ Add Preferred Topics", b"add_preferred_topics"),
             Button.inline("➖ Add Avoided Topics", b"add_avoided_topics")],
            [Button.inline("🗑️ Clear Topic Preferences", b"clear_topics")],
            [Button.inline("◀️ Back", b"customize")]
        ]
        
        # Send a new message instead of editing
        await event.respond(topics_text, buttons=buttons)
        user_menu_state[user_id] = 'customize_topics'

    @client.on(events.CallbackQuery(data=b"customize_instructions"))
    async def customize_instructions_handler(event):
        user_id = event.sender_id
        
        # Get current custom instructions
        settings = get_user_customization(user_id)
        current_instructions = settings.get('custom_instructions', '')
        
        instructions_text = f"""
📝 **Custom Instructions**
        
Custom instructions let you provide specific guidance on how I should respond.
        
Current instructions:
{current_instructions or "No custom instructions set."}
        
Type your new custom instructions, or click "Clear Instructions" to remove them.
        """
        
        buttons = [
            [Button.inline("🗑️ Clear Instructions", b"clear_instructions")],
            [Button.inline("◀️ Back", b"customize")]
        ]
        
        # Send a new message instead of editing
        await event.respond(instructions_text, buttons=buttons)
        user_sessions[user_id]['awaiting_custom_instructions'] = True
        user_menu_state[user_id] = 'customize_instructions'

    @client.on(events.CallbackQuery(data=b"pause_learning"))
    async def pause_learning_handler(event):
        user_id = event.sender_id
        
        duration_text = """
⏱️ **Pause Learning Duration**
        
How long would you like to pause learning?
        """
        
        buttons = [
            [Button.inline("1 Hour", b"pause_1h"),
             Button.inline("6 Hours", b"pause_6h")],
            [Button.inline("24 Hours", b"pause_24h"),
             Button.inline("Until Resumed", b"pause_indefinite")],
            [Button.inline("◀️ Cancel", b"learning")]
        ]
        
        # Send a new message instead of editing
        await event.respond(duration_text, buttons=buttons)
        user_menu_state[user_id] = 'pause_learning_duration'

    @client.on(events.CallbackQuery(data=b"resume_learning"))
    async def resume_learning_handler(event):
        user_id = event.sender_id
        
        # Resume learning
        if resume_learning(user_id):
            success_text = """
✅ **Learning Resumed**
            
I've resumed learning from our conversations. I'll now:
• Store facts about you to provide better responses
• Learn your preferences and interests over time
• Use your messages to understand you better
            """
        else:
            success_text = """
❌ **Error**
            
I couldn't resume learning. Please try again later.
            """
        
        buttons = [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'learning'

    @client.on(events.CallbackQuery(data=b"pause_1h"))
    async def pause_1h_handler(event):
        user_id = event.sender_id
        
        # Pause learning for 1 hour
        if suspend_learning(user_id, 1):
            success_text = """
✅ **Learning Paused for 1 Hour**
            
I've paused learning from our conversations for 1 hour. During this time:
• I won't store new facts about you
• I won't update my understanding of your preferences
• Your messages are still processed but not saved for learning
            
Learning will automatically resume after 1 hour, or you can resume it manually.
            """
        else:
            success_text = """
❌ **Error**
            
I couldn't pause learning. Please try again later.
            """
        
        buttons = [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'learning'

    @client.on(events.CallbackQuery(data=b"pause_6h"))
    async def pause_6h_handler(event):
        user_id = event.sender_id
        
        # Pause learning for 6 hours
        if suspend_learning(user_id, 6):
            success_text = """
✅ **Learning Paused for 6 Hours**
            
I've paused learning from our conversations for 6 hours. During this time:
• I won't store new facts about you
• I won't update my understanding of your preferences
• Your messages are still processed but not saved for learning
            
Learning will automatically resume after 6 hours, or you can resume it manually.
            """
        else:
            success_text = """
❌ **Error**
            
I couldn't pause learning. Please try again later.
            """
        
        buttons = [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'learning'

    @client.on(events.CallbackQuery(data=b"pause_24h"))
    async def pause_24h_handler(event):
        user_id = event.sender_id
        
        # Pause learning for 24 hours
        if suspend_learning(user_id, 24):
            success_text = """
✅ **Learning Paused for 24 Hours**
            
I've paused learning from our conversations for 24 hours. During this time:
• I won't store new facts about you
• I won't update my understanding of your preferences
• Your messages are still processed but not saved for learning
            
Learning will automatically resume after 24 hours, or you can resume it manually.
            """
        else:
            success_text = """
❌ **Error**
            
I couldn't pause learning. Please try again later.
            """
        
        buttons = [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'learning'

    @client.on(events.CallbackQuery(data=b"pause_indefinite"))
    async def pause_indefinite_handler(event):
        user_id = event.sender_id
        
        # Pause learning indefinitely
        if suspend_learning(user_id):
            success_text = """
✅ **Learning Paused Indefinitely**
            
I've paused learning from our conversations until you manually resume it. During this time:
• I won't store new facts about you
• I won't update my understanding of your preferences
• Your messages are still processed but not saved for learning
            
You can resume learning at any time using the /learning command.
            """
        else:
            success_text = """
❌ **Error**
            
I couldn't pause learning. Please try again later.
            """
        
        buttons = [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'learning'

    @client.on(events.CallbackQuery(data=b"personality_friendly"))
    async def personality_friendly_handler(event):
        user_id = event.sender_id
        
        # Update customization settings
        settings = get_user_customization(user_id)
        settings['personality_type'] = 'friendly'
        update_user_customization(user_id, settings)
        
        success_text = """
✅ **Personality Updated**
            
I'll now use a friendly and casual personality in our conversations!
            """
        
        buttons = [Button.inline("◀️ Back to Customization", b"customize")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'customize'

    @client.on(events.CallbackQuery(data=b"personality_intellectual"))
    async def personality_intellectual_handler(event):
        user_id = event.sender_id
        
        # Update customization settings
        settings = get_user_customization(user_id)
        settings['personality_type'] = 'intellectual'
        update_user_customization(user_id, settings)
        
        success_text = """
✅ **Personality Updated**
            
I'll now use a more intellectual and thoughtful personality in our conversations!
            """
        
        buttons = [Button.inline("◀️ Back to Customization", b"customize")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'customize'

    @client.on(events.CallbackQuery(data=b"personality_humorous"))
    async def personality_humorous_handler(event):
        user_id = event.sender_id
        
        # Update customization settings
        settings = get_user_customization(user_id)
        settings['personality_type'] = 'humorous'
        update_user_customization(user_id, settings)
        
        success_text = """
✅ **Personality Updated**
            
I'll now use a more humorous and playful personality in our conversations!
            """
        
        buttons = [Button.inline("◀️ Back to Customization", b"customize")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'customize'

    @client.on(events.CallbackQuery(data=b"personality_professional"))
    async def personality_professional_handler(event):
        user_id = event.sender_id
        
        # Update customization settings
        settings = get_user_customization(user_id)
        settings['personality_type'] = 'professional'
        update_user_customization(user_id, settings)
        
        success_text = """
✅ **Personality Updated**
            
I'll now use a more professional and formal personality in our conversations!
            """
        
        buttons = [Button.inline("◀️ Back to Customization", b"customize")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'customize'

    @client.on(events.CallbackQuery(data=b"style_conversational"))
    async def style_conversational_handler(event):
        user_id = event.sender_id
        
        # Update customization settings
        settings = get_user_customization(user_id)
        settings['response_style'] = 'conversational'
        update_user_customization(user_id, settings)
        
        success_text = """
✅ **Response Style Updated**
            
I'll now use a conversational style in our interactions!
            """
        
        buttons = [Button.inline("◀️ Back to Customization", b"customize")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'customize'

    @client.on(events.CallbackQuery(data=b"style_detailed"))
    async def style_detailed_handler(event):
        user_id = event.sender_id
        
        # Update customization settings
        settings = get_user_customization(user_id)
        settings['response_style'] = 'detailed'
        update_user_customization(user_id, settings)
        
        success_text = """
✅ **Response Style Updated**
            
I'll now use a more detailed and comprehensive style in our interactions!
            """
        
        buttons = [Button.inline("◀️ Back to Customization", b"customize")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'customize'

    @client.on(events.CallbackQuery(data=b"style_concise"))
    async def style_concise_handler(event):
        user_id = event.sender_id
        
        # Update customization settings
        settings = get_user_customization(user_id)
        settings['response_style'] = 'concise'
        update_user_customization(user_id, settings)
        
        success_text = """
✅ **Response Style Updated**
            
I'll now use a more concise and to-the-point style in our interactions!
            """
        
        buttons = [Button.inline("◀️ Back to Customization", b"customize")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'customize'

    @client.on(events.CallbackQuery(data=b"style_creative"))
    async def style_creative_handler(event):
        user_id = event.sender_id
        
        # Update customization settings
        settings = get_user_customization(user_id)
        settings['response_style'] = 'creative'
        update_user_customization(user_id, settings)
        
        success_text = """
✅ **Response Style Updated**
            
I'll now use a more creative and imaginative style in our interactions!
            """
        
        buttons = [Button.inline("◀️ Back to Customization", b"customize")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'customize'

    @client.on(events.CallbackQuery(data=b"add_preferred_topics"))
    async def add_preferred_topics_handler(event):
        user_id = event.sender_id
        
        topics_text = """
➕ **Add Preferred Topics**
        
Please list topics you're interested in, separated by commas.
For example: "technology, science, movies, cooking"
        
Type your preferred topics now:
        """
        
        buttons = [Button.inline("◀️ Cancel", b"customize_topics")]
        
        # Send a new message instead of editing
        await event.respond(topics_text, buttons=buttons)
        user_sessions[user_id]['awaiting_preferred_topics'] = True
        user_menu_state[user_id] = 'add_preferred_topics'

    @client.on(events.CallbackQuery(data=b"add_avoided_topics"))
    async def add_avoided_topics_handler(event):
        user_id = event.sender_id
        
        topics_text = """
➖ **Add Avoided Topics**
        
Please list topics you'd prefer to avoid, separated by commas.
For example: "politics, religion, sports"
        
Type your avoided topics now:
        """
        
        buttons = [Button.inline("◀️ Cancel", b"customize_topics")]
        
        # Send a new message instead of editing
        await event.respond(topics_text, buttons=buttons)
        user_sessions[user_id]['awaiting_avoided_topics'] = True
        user_menu_state[user_id] = 'add_avoided_topics'

    @client.on(events.CallbackQuery(data=b"clear_topics"))
    async def clear_topics_handler(event):
        user_id = event.sender_id
        
        # Update customization settings
        settings = get_user_customization(user_id)
        settings['preferred_topics'] = ''
        settings['avoided_topics'] = ''
        update_user_customization(user_id, settings)
        
        success_text = """
✅ **Topic Preferences Cleared**
            
I've cleared all your topic preferences and avoidances.
            """
        
        buttons = [Button.inline("◀️ Back to Customization", b"customize")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'customize'

    @client.on(events.CallbackQuery(data=b"clear_instructions"))
    async def clear_instructions_handler(event):
        user_id = event.sender_id
        
        # Update customization settings
        settings = get_user_customization(user_id)
        settings['custom_instructions'] = ''
        update_user_customization(user_id, settings)
        
        success_text = """
✅ **Custom Instructions Cleared**
            
I've cleared your custom instructions.
            """
        
        buttons = [Button.inline("◀️ Back to Customization", b"customize")]
        
        # Send a new message instead of editing
        await event.respond(success_text, buttons=buttons)
        user_menu_state[user_id] = 'customize'

    @client.on(events.CallbackQuery(data=b"terms"))
    async def terms_handler(event):
        user_id = event.sender_id
        
        terms_text = """
🤝 **Our Friendship Rules:**
        
1. Be kind to each other
2. No bad vibes allowed
3. Have fun together!
4. I'll remember our chats to serve you better
5. You can delete your data anytime
        
That's it! Simple, right? 😄
        """
        
        buttons = [Button.inline("◀️ Back", b"back_to_menu")]
        
        # Send a new message instead of editing
        await event.respond(terms_text, buttons=buttons)
        user_menu_state[user_id] = 'terms'

    @client.on(events.CallbackQuery(data=b"help"))
    async def help_handler(event):
        user_id = event.sender_id
        
        # Get all commands
        commands = get_available_commands()
        command_list = "\n".join([f"• {cmd['command']} - {cmd['description']}" for cmd in commands])
        
        help_text = f"""
❓ **{BOT_NAME} Help Guide**

**Available Commands:**
{command_list}
        
**Quick Tips:**
• Just type a message to chat with me
• Use inline buttons for navigation
• I remember our conversations and learn from them
• Ask me anything, and I'll do my best to help!
• Use /code to generate code snippets
• Add me to groups for group chat functionality
• Customize my behavior with /customize
• Control my learning with /learning
        
Need more help? Join our community: {SOCIAL_LINKS["📢 Community"]}
        """
        
        buttons = [Button.inline("◀️ Back", b"back_to_menu")]
        
        # Send a new message instead of editing
        await event.respond(help_text, buttons=buttons)
        user_menu_state[user_id] = 'help'

    @client.on(events.CallbackQuery(data=b"about"))
    async def about_handler(event):
        user_id = event.sender_id
        
        about_text = f"""
**ℹ️ About {BOT_NAME} :**
Copyright (c) 2025 CodeAra

Designed by {COMPANY} in Harrach

**🧑‍💻 Owner:** {FOUNDER}
**🔢 Version:** {BOT_VERSION}
**📅 Build Date:** 19-04-2025
**⬆️ Update Date:** {DATE_UPDATE}
**🔤 Build ID:** {BUILD_ID}

**✨ What's New in v3.5.0**
• Enhanced file handling system 📁
• Improved group interaction capabilities 👥
• Added learning suspension feature 🧠
• New bot customization options 🎭
• Refined menu management system 📋
• Better file content processing 🔍
• Expanded settings integration 🔧

        """
        
        buttons = [Button.inline("◀️ Back", b"back_to_menu")]
        
        # Send a new message instead of editing
        await event.respond(about_text, buttons=buttons)
        user_menu_state[user_id] = 'about'

    @client.on(events.CallbackQuery(data=b"settings"))
    async def settings_handler(event):
        user_id = event.sender_id
        
        settings_text = """
🔧 **Settings**
        
Choose an option:
        """
        
        buttons = [
            [Button.inline("🧠 Memory Settings", b"memory_settings"),
             Button.inline("🗂️ Data Management", b"data_management")],
            [Button.inline("🎭 Bot Customization", b"customize"),
             Button.inline("👥 Group Settings", b"group_settings")],
            [Button.inline("🔄 Learning Control", b"learning")],
            [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        ]
        
        # Send a new message instead of editing
        await event.respond(settings_text, buttons=buttons)
        user_menu_state[user_id] = 'settings'

    @client.on(events.CallbackQuery(data=b"chat"))
    async def chat_handler(event):
        user_id = event.sender_id
        first_name = await get_user_name(user_id)
        
        chat_text = f"""
💬 **Chat Mode**
        
Hey {first_name}! I'm ready to chat with you. Just type a message, and I'll respond!
        
Need ideas? You could:
• Ask me a question 🗨️
• Tell me about your day 💡
• Discuss a topic you're interested in 📄
• Get help with a problem 🪛
        
I'll remember our conversation and learn from it.
        """
        
        buttons = [
            [Button.inline("🔄 New Conversation", b"new_conversation")],
            [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        ]
        
        # Send a new message instead of editing
        await event.respond(chat_text, buttons=buttons)
        user_menu_state[user_id] = 'chat'

    @client.on(events.CallbackQuery(data=b"new_conversation"))
    async def new_conversation_handler(event):
        user_id = event.sender_id
        first_name = await get_user_name(user_id)
        
        # Reset conversation context
        start_new_conversation(user_id)
        
        new_chat_text = f"""
        🔄 Started a fresh conversation, {first_name}!
        
        What would you like to talk about today?
        """
        
        buttons = [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        
        # Send a new message instead of editing
        await event.respond(new_chat_text, buttons=buttons)
        user_menu_state[user_id] = 'chat'

    @client.on(events.CallbackQuery(data=b"gen_image"))
    async def gen_image_handler(event):
        user_id = event.sender_id
        
        image_prompt_text = """
🎨 **Enhanced Image Generation**
        
Describe the image you'd like me to create:
• Be specific about what you want to see
• Include details about style, mood, and elements
• Add art style references (e.g., "watercolor", "digital art", "photorealistic")
• Example: "A futuristic city with flying cars and neon lights, cyberpunk style, dramatic lighting"
       
Type your description now, and I'll create the image!
        """
        
        buttons = [Button.inline("◀️ Back", b"back_to_menu")]
        
        # Send a new message instead of editing
        await event.respond(image_prompt_text, buttons=buttons)
        user_sessions[user_id]['awaiting_image_prompt'] = True
        user_menu_state[user_id] = 'image_gen'

    @client.on(events.CallbackQuery(data=b"gen_code"))
    async def gen_code_handler(event):
        user_id = event.sender_id
        
        code_prompt_text = """
💻 **Code Generation**
        
Describe what code you'd like me to create:
• Be specific about functionality and language
• Include details about features and requirements
• Example: "Create a Python function that sorts a list of dictionaries by a specific key"
        
Type your description now, and I'll generate the code!
        """
        
        buttons = [Button.inline("◀️ Back", b"back_to_menu")]
        
        # Send a new message instead of editing
        await event.respond(code_prompt_text, buttons=buttons)
        user_sessions[user_id]['awaiting_code_prompt'] = True
        user_menu_state[user_id] = 'code_gen'

    @client.on(events.CallbackQuery(data=b"files"))
    async def files_handler(event):
        user_id = event.sender_id
        
        # Get user's files
        files = get_user_files(user_id)
        
        if files:
            # Format file list
            file_list = "\n".join([
                f"• {file[1]} ({file[2]}, {file[3]/1024:.1f} KB)" 
                for file in files[:5]
            ])
            
            files_text = f"""
📁 **Your Files**
            
Recent uploads:
{file_list}
            
What would you like to do?
            """
        else:
            files_text = """
📁 **Files**
            
You haven't uploaded any files yet.
            
You can send me files up to 50MB in size. I'll store them safely for you.
            """
        
        buttons = [
            [Button.inline("📤 Upload New File", b"upload_file"),
             Button.inline("📋 View All Files", b"view_files")],
            [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        ]
        
        # Send a new message instead of editing
        await event.respond(files_text, buttons=buttons)
        user_menu_state[user_id] = 'files'

    @client.on(events.CallbackQuery(data=b"upload_file"))
    async def upload_file_handler(event):
        user_id = event.sender_id
        
        upload_text = """
📤 **Upload a File**
        
You can send me any file up to 50MB! I'll keep it safe for you.
        
Supported file types:
• Images (jpg, png, etc.) 🖼️
• Documents (pdf, docx, txt, etc.) 📄
• Audio files 🎵
• Video files (small clips) 📹
        
Just send the file as an attachment.
        """
        
        buttons = [Button.inline("◀️ Back", b"files")]
        
        # Send a new message instead of editing
        await event.respond(upload_text, buttons=buttons)
        user_sessions[user_id]['awaiting_file'] = True
        user_menu_state[user_id] = 'upload_file'

    @client.on(events.CallbackQuery(data=b"view_files"))
    async def view_files_handler(event):
        user_id = event.sender_id
        
        # Get all user files
        files = get_user_files(user_id, 20)
        
        if files:
            # Format file list with more details
            file_list = "\n".join([
                f"• {i+1}. {file[1]} ({file[2]}, {file[3]/1024:.1f} KB, {file[4]})" 
                for i, file in enumerate(files)
            ])
            
            files_text = f"""
📋 **All Your Files**
            
{file_list}
            
To access a file, type its number (e.g., "1" for the first file).
            """
        else:
            files_text = """
📋 **Files**
            
You haven't uploaded any files yet.
            
You can send me files up to 50MB in size. I'll store them safely for you.
            """
        
        buttons = [Button.inline("◀️ Back", b"files")]
        
        # Send a new message instead of editing
        await event.respond(files_text, buttons=buttons)
        user_menu_state[user_id] = 'view_files'

    @client.on(events.CallbackQuery(data=b"memory_settings"))
    async def memory_settings_handler(event):
        user_id = event.sender_id
        
        memory_text = """
🧠 **Memory Settings**
        
Control how I remember and learn from our conversations:
        """
        
        buttons = [
            [Button.inline("👁️ View My Data", b"view_data"),
             Button.inline("🗑️ Delete My Data", b"delete_data")],
            [Button.inline("🔄 Learning Control", b"learning")],
            [Button.inline("◀️ Back to Settings", b"settings")]
        ]
        
        # Send a new message instead of editing
        await event.respond(memory_text, buttons=buttons)
        user_menu_state[user_id] = 'memory_settings'

    @client.on(events.CallbackQuery(data=b"group_settings"))
    async def group_settings_handler(event):
        user_id = event.sender_id
        
        groups_text = """
👥 **Group Management**
            
This feature is for managing my behavior in group chats.
            
To use this feature:
1. Add me to a group
2. Make me an admin (for best functionality)
3. Use /groups command in the group to configure settings
            
In private chat, you can:
            """
            
        buttons = [
            [Button.inline("📋 View My Groups", b"view_groups"),
             Button.inline("📊 View All Groups", b"view_all_groups")],
            [Button.inline("◀️ Back to Settings", b"settings")]
        ]
        
        # Send a new message instead of editing
        await event.respond(groups_text, buttons=buttons)
        user_menu_state[user_id] = 'group_settings'

    @client.on(events.CallbackQuery(data=b"view_groups"))
    async def view_groups_handler(event):
        user_id = event.sender_id
        
        # Get user's groups
        groups = get_user_groups(user_id)
        
        if groups:
            # Format group list
            group_list = "\n".join([
                f"• {group[1]} ({group[2]} messages by you, {group[3]} total)" 
                for group in groups[:10]
            ])
            
            groups_text = f"""
📋 **Your Groups**
            
Groups where you and I are both members:
{group_list}
            
To manage a group, use the /groups command in that group.
            """
        else:
            groups_text = """
📋 **Your Groups**
            
You're not a member of any groups with me yet.
            
To add me to a group:
1. Open the group in Telegram
2. Tap the group name at the top
3. Tap "Add members"
4. Search for me (@GlitchAI_1_Bot) and add me
            """
        
        buttons = [Button.inline("◀️ Back", b"group_settings")]
        
        # Send a new message instead of editing
        await event.respond(groups_text, buttons=buttons)
        user_menu_state[user_id] = 'view_groups'

    @client.on(events.CallbackQuery(data=b"view_all_groups"))
    async def view_all_groups_handler(event):
        user_id = event.sender_id
        
        # Get all active groups
        groups = get_all_active_groups()
        
        if groups:
            # Format group list
            group_list = "\n".join([
                f"• {group[1]} ({group[2]} messages)" 
                for group in groups[:15]
            ])
            
            groups_text = f"""
📊 **All Active Groups**
            
Groups where I'm active:
{group_list}
            
These are public groups where I've been added.
            """
        else:
            groups_text = """
📊 **All Active Groups**
            
I'm not active in any groups yet.
            """
        
        buttons = [Button.inline("◀️ Back", b"group_settings")]
        
        # Send a new message instead of editing
        await event.respond(groups_text, buttons=buttons)
        user_menu_state[user_id] = 'view_all_groups'

    @client.on(events.CallbackQuery(data=b"toggle_group_response"))
    async def toggle_group_response_handler(event):
        # This handler is for group settings, so we need to get the group ID
        user_id = event.sender_id
        chat = await event.get_chat()
        
        if not isinstance(chat, types.Channel):  # Groups are represented as Channel in Telethon
            await event.answer("This button only works in group chats")
            return
        
        group_id = chat.id
        
        # Load current settings
        if group_id not in group_settings:
            load_group_settings(group_id)
        
        settings = group_settings.get(group_id, {})
        
        # Toggle response mode (cycle through options)
        if settings.get('respond_to_all', False):
            # Currently responding to all, switch to mentions only
            new_settings = {
                'respond_to_all': False,
                'respond_to_mentions': True,
                'respond_to_commands': True
            }
            mode_text = "Mentions & Commands Only"
        elif settings.get('respond_to_mentions', True):
            # Currently responding to mentions, switch to commands only
            new_settings = {
                'respond_to_all': False,
                'respond_to_mentions': False,
                'respond_to_commands': True
            }
            mode_text = "Commands Only"
        else:
            # Currently responding to commands only, switch to all messages
            new_settings = {
                'respond_to_all': True,
                'respond_to_mentions': True,
                'respond_to_commands': True
            }
            mode_text = "All Messages"
        
        # Update settings
        update_group_settings(group_id, new_settings)
        
        await event.answer(f"Response mode changed to: {mode_text}")
        
        # Refresh the group settings display
        await groups_command_handler(event)

    @client.on(events.CallbackQuery(data=b"toggle_welcome"))
    async def toggle_welcome_handler(event):
        # This handler is for group settings, so we need to get the group ID
        user_id = event.sender_id
        chat = await event.get_chat()
        
        if not isinstance(chat, types.Channel):  # Groups are represented as Channel in Telethon
            await event.answer("This button only works in group chats")
            return
        
        group_id = chat.id
        
        # Load current settings
        if group_id not in group_settings:
            load_group_settings(group_id)
        
        settings = group_settings.get(group_id, {})
        
        # Toggle welcome setting
        welcome_new = not settings.get('welcome_new_members', True)
        
        # Update settings
        update_group_settings(group_id, {'welcome_new_members': welcome_new})
        
        await event.answer(f"Welcome messages: {'Enabled' if welcome_new else 'Disabled'}")
        
        # Refresh the group settings display
        await groups_command_handler(event)

    @client.on(events.CallbackQuery(data=b"edit_welcome_msg"))
    async def edit_welcome_msg_handler(event):
        user_id = event.sender_id
        chat = await event.get_chat()
        
        if not isinstance(chat, types.Channel):  # Groups are represented as Channel in Telethon
            await event.answer("This button only works in group chats")
            return
        
        group_id = chat.id
        
        # Load current settings
        if group_id not in group_settings:
            load_group_settings(group_id)
        
        settings = group_settings.get(group_id, {})
        
        # Get current welcome message
        current_msg = settings.get('welcome_message', f"Welcome to the group! I'm {BOT_NAME}, your friendly AI assistant. Tag me or use commands to interact with me!")
        
        welcome_text = f"""
✏️ **Edit Welcome Message**
        
Current welcome message:
"{current_msg}"
        
Reply with your new welcome message. This will be sent to new members when they join the group.
        """
        
        # Send a new message instead of editing
        await event.respond(welcome_text)
        
        # Set flag to await new welcome message
        user_sessions[user_id]['awaiting_welcome_message'] = True
        user_sessions[user_id]['group_id'] = group_id

    @client.on(events.CallbackQuery(data=b"view_members"))
    async def view_members_handler(event):
        chat = await event.get_chat()
        
        if not isinstance(chat, types.Channel):  # Groups are represented as Channel in Telethon
            await event.answer("This button only works in group chats")
            return
        
        group_id = chat.id
        group_name = chat.title
        
        # Get group members from database
        members = get_group_members(group_id)
        
        if members:
            # Format member list
            member_list = "\n".join([
                f"• {member[1]} ({member[4]} messages)" 
                for member in members[:10]
            ])
            
            members_text = f"""
👥 **Members of {group_name}**
            
Top active members:
{member_list}
            
Total tracked members: {len(members)}
            """
        else:
            members_text = f"""
👥 **Members of {group_name}**
            
No member activity tracked yet.
Members will appear here as they interact with me in the group.
            """
        
        buttons = [Button.inline("◀️ Back", b"back_to_group_settings")]
        
        # Send a new message instead of editing
        await event.respond(members_text, buttons=buttons)

    @client.on(events.CallbackQuery(data=b"back_to_group_settings"))
    async def back_to_group_settings_handler(event):
        # Just call the groups command handler to refresh the view
        await groups_command_handler(event)

    @client.on(events.CallbackQuery(data=b"data_management"))
    async def data_management_handler(event):
        user_id = event.sender_id
        
        # Get user stats
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM conversations WHERE user_id = ?", (user_id,))
        message_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_facts WHERE user_id = ?", (user_id,))
        facts_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM files WHERE user_id = ?", (user_id,))
        files_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT first_seen FROM users WHERE user_id = ?", (user_id,))
        first_seen_row = cursor.fetchone()
        first_seen = datetime.fromisoformat(first_seen_row[0]) if first_seen_row else datetime.now()
        
        conn.close()
        
        days_known = (datetime.now() - first_seen).days or 1
        
        data_text = f"""
📊 **Your Data**
        
Messages exchanged: {message_count}
Facts I've learned: {facts_count}
Files stored: {files_count}
Days we've known each other: {days_known}
        
What would you like to do?
        """
        
        buttons = [
            [Button.inline("📤 Export Data", b"export_data"),
             Button.inline("🗑️ Delete Data", b"delete_data")],
            [Button.inline("◀️ Back to Settings", b"settings")]
        ]
        
        # Send a new message instead of editing
        await event.respond(data_text, buttons=buttons)
        user_menu_state[user_id] = 'data_management'

    @client.on(events.CallbackQuery(data=b"view_data"))
    async def view_data_handler(event):
        user_id = event.sender_id
        
        # Get user facts summary
        await event.respond("🧠 Gathering what I know about you...")
        summary = await get_user_facts_summary(user_id)
        
        buttons = [Button.inline("◀️ Back", b"memory_settings")]
        
        # Send a new message instead of editing
        await event.respond(summary, buttons=buttons)
        user_menu_state[user_id] = 'view_data'

    @client.on(events.CallbackQuery(data=b"export_data"))
    async def export_data_handler(event):
        user_id = event.sender_id
        
        await event.respond("📤 Preparing your data export... Please wait.")
        
        filename = await export_conversations(user_id)
        if filename:
            with open(filename, 'rb') as f:
                await client.send_file(
                    user_id,
                    f,
                    caption="Here's your conversation history export! 📊",
                    buttons=Button.inline("◀️ Back", b"data_management")
                )
            
            # Send a follow-up message to explain the data
            await client.send_message(
                user_id,
                """
📋 **About Your Data Export**
                
The JSON file contains:
• All your conversations with me 🗨️
• Message timestamps 🕒
• Conversation IDs and message numbers 🔢
• File information 📁

You can open this file with any text editor or JSON viewer.
                """
            )
        else:
            await event.respond(
                "Sorry, I couldn't export your data right now. Please try again later.",
                buttons=Button.inline("◀️ Back", b"data_management")
            )

    @client.on(events.CallbackQuery(data=b"delete_data"))
    async def delete_data_handler(event):
        user_id = event.sender_id
        
        delete_text = """
⚠️ **Delete Your Data**
        
This will delete ALL your data, including:
• Conversation history 🕒
• Learned facts about you 🧠
• Uploaded files 📁
• Preferences and settings 🔧
        
This action CANNOT be undone. Are you sure?
        """
        
        buttons = [
            [Button.inline("✅ Yes, delete everything", b"confirm_delete"),
             Button.inline("❌ No, keep my data", b"data_management")]
        ]
        
        # Send a new message instead of editing
        await event.respond(delete_text, buttons=buttons)
        user_menu_state[user_id] = 'delete_data'

    @client.on(events.CallbackQuery(data=b"confirm_delete"))
    async def confirm_delete_handler(event):
        user_id = event.sender_id
        
        await event.respond("🗑️ Deleting your data... Please wait.")
        
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Delete conversations
            cursor.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
            
            # Delete facts
            cursor.execute("DELETE FROM user_facts WHERE user_id = ?", (user_id,))
            
            # Delete files
            cursor.execute("DELETE FROM files WHERE user_id = ?", (user_id,))
            
            # Delete customization settings
            cursor.execute("DELETE FROM bot_customization WHERE user_id = ?", (user_id,))
            
            # Reset user preferences but keep the user entry
            cursor.execute(
                """
                UPDATE users 
                SET personality_traits = NULL, preferences = NULL, interests = NULL,
                learning_disabled = 0, learning_disabled_until = NULL, customization_settings = NULL
                WHERE user_id = ?
                """,
                (user_id,)
            )
            
            conn.commit()
            conn.close()
            
            # Reset conversation context
            if user_id in conversation_contexts:
                del conversation_contexts[user_id]
            start_new_conversation(user_id)
            
            # Reset learning suspension settings
            if user_id in learning_suspension:
                del learning_suspension[user_id]
            
            # Reset customization settings
            if user_id in user_customization:
                del user_customization[user_id]
            
            success_text = """
            ✅ **Data Deleted Successfully**
            
All your data has been deleted. I've forgotten:
• Our conversation history 🕒
• Facts I learned about you 🧠
• Your uploaded files 📁
• Your preferences and interests 👁️‍🗨️
• Your customization settings 🎭
            
We're starting fresh!
            """
            
            buttons = [Button.inline("◀️ Back to Menu", b"back_to_menu")]
            await event.respond(success_text, buttons=buttons)
            
        except Exception as e:
            logger.error(f"Error deleting user data: {e}")
            error_text = "Sorry, I couldn't delete your data right now. Please try again later."
            buttons = [Button.inline("◀️ Back", b"data_management")]
            await event.respond(error_text, buttons=buttons)

    @client.on(events.CallbackQuery(data=b"back_to_menu"))
    async def back_to_menu_handler(event):
        user_id = event.sender_id
        first_name = await get_user_name(user_id)
        
        menu_msg = f"""
🌟 {BOT_NAME} Menu 🌟
        
Hey {first_name}! What would you like to do today?
        """
        
        buttons = [
            [Button.inline("💬 Chat", b"chat"),
             Button.inline("🎨 Create Image", b"gen_image")],
            [Button.inline("💻 Generate Code", b"gen_code"),
             Button.inline("📁 Files", b"files")],
            [Button.inline("❓ Help", b"help"),
             Button.inline("ℹ️ About", b"about")],
            [Button.inline("🔧 Settings", b"settings")]
        ]
        
        # Send a new message instead of editing
        await event.respond(menu_msg, buttons=buttons)
        user_menu_state[user_id] = 'main'

    @client.on(events.NewMessage(pattern='/upload'))
    async def upload_handler(event):
        user_id = event.sender_id
        log_command(user_id, '/upload')
        
        upload_text = """
📁 **File Upload**
        
You can send me any file up to 50MB! I'll keep it safe for you.
        
Supported file types:
• Images (jpg, png, etc.) 🖼️
• Documents (pdf, docx, txt, etc.) 📄
• Audio files 🎵
• Video files (small clips) 📹
        
Just send the file as an attachment.
        """
        
        buttons = [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        
        # Send a new message
        message = await event.respond(upload_text, buttons=buttons)
        user_sessions[user_id]['awaiting_file'] = True
        user_menu_state[user_id] = 'upload'

    @client.on(events.NewMessage(pattern='/generate'))
    async def generate_handler(event):
        user_id = event.sender_id
        log_command(user_id, '/generate')
        
        generate_text = """
🎨 **Enhanced Image Generation**
        
Describe the image you'd like me to create:
• Be specific about what you want to see
• Include details about style, mood, and elements
• Add art style references (e.g., "watercolor", "digital art", "photorealistic")
• Example: "A futuristic city with flying cars and neon lights, cyberpunk style, dramatic lighting"
        
Type your description now, and I'll create the image!
        """
        
        buttons = [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        
        # Send a new message
        message = await event.respond(generate_text, buttons=buttons)
        user_sessions[user_id]['awaiting_image_prompt'] = True
        user_menu_state[user_id] = 'image_gen'

    @client.on(events.NewMessage(pattern='/export'))
    async def export_handler(event):
        user_id = event.sender_id
        log_command(user_id, '/export')
        
        await event.respond("📤 Preparing your data export... Please wait.")
        
        filename = await export_conversations(user_id)
        if filename:
            with open(filename, 'rb') as f:
                await client.send_file(
                    user_id,
                    f,
                    caption="Here's your conversation history export! 📊"
                )
        else:
            await event.respond("Sorry, I couldn't export your data right now. Please try again later.")

    @client.on(events.NewMessage(pattern='/forget'))
    async def forget_handler(event):
        user_id = event.sender_id
        log_command(user_id, '/forget')
        
        delete_text = """
⚠️ **Delete Your Data**
        
This will delete ALL your data, including:
• Conversation history 🕒
• Learned facts about you 🧠
• Uploaded files 📁
• Preferences and settings 🔧
        
This action CANNOT be undone. Are you sure?
        """
        
        buttons = [
            [Button.inline("✅ Yes, delete everything", b"confirm_delete"),
             Button.inline("❌ No, keep my data", b"back_to_menu")]
        ]
        
        message = await event.respond(delete_text, buttons=buttons)
        user_menu_state[user_id] = 'delete_data'

    @client.on(events.NewMessage(pattern='/customize'))
    async def customize_handler(event):
        user_id = event.sender_id
        log_command(user_id, '/customize')
        
        # Get current customization settings
        settings = get_user_customization(user_id)
        
        customize_text = f"""
🎭 **Bot Customization**
        
Current settings:
• Personality: {settings.get('personality_type', 'friendly')}
• Response style: {settings.get('response_style', 'conversational')}
• Preferred topics: {settings.get('preferred_topics', 'No specific preferences')}
• Topics to avoid: {settings.get('avoided_topics', 'No specific avoidances')}
        
What would you like to customize?
        """
        
        buttons = [
            [Button.inline("🤖 Personality", b"customize_personality"),
             Button.inline("💬 Response Style", b"customize_style")],
            [Button.inline("📋 Topics", b"customize_topics"),
             Button.inline("📝 Custom Instructions", b"customize_instructions")],
            [Button.inline("◀️ Back to Menu", b"back_to_menu")]
        ]
        
        message = await event.respond(customize_text, buttons=buttons)
        user_menu_state[user_id] = 'customize'

    @client.on(events.NewMessage(pattern='/learning'))
    async def learning_handler(event):
        user_id = event.sender_id
        log_command(user_id, '/learning')
        
        # Check current learning status
        is_suspended = is_learning_suspended(user_id)
        
        if is_suspended:
            # Learning is currently suspended
            status_text = """
🧠 **Learning Status: PAUSED**
            
I'm currently not learning from our conversations. This means:
• I won't store new facts about you
• I won't update my understanding of your preferences
• Your messages are still processed but not saved for learning
            
What would you like to do?
            """
            
            buttons = [
                [Button.inline("▶️ Resume Learning", b"resume_learning")],
                [Button.inline("◀️ Back to Menu", b"back_to_menu")]
            ]
        else:
            # Learning is active
            status_text = """
🧠 **Learning Status: ACTIVE**
            
I'm currently learning from our conversations. This means:
• I store facts about you to provide better responses
• I learn your preferences and interests over time
• Your messages help me understand you better
            
Would you like to temporarily pause learning?
            """
            
            buttons = [
                [Button.inline("⏸️ Pause Learning", b"pause_learning")],
                [Button.inline("◀️ Back to Menu", b"back_to_menu")]
            ]
        
        message = await event.respond(status_text, buttons=buttons)
        user_menu_state[user_id] = 'learning'

    @client.on(events.NewMessage(pattern='/groups'))
    async def groups_command_handler(event):
        """Handle the /groups command to manage group settings"""
        user_id = event.sender_id
        log_command(user_id, '/groups')
        
        # Check if this is a private chat
        if event.is_private:
            groups_text = """
👥 **Group Management**
            
This feature is for managing my behavior in group chats.
            
To use this feature:
1. Add me to a group
2. Make me an admin (for best functionality)
3. Use this command in the group to configure settings
            
In private chat, you can:
            """
            
            buttons = [
                [Button.inline("📋 View My Groups", b"view_groups"),
                 Button.inline("📊 View All Groups", b"view_all_groups")],
                [Button.inline("◀️ Back to Menu", b"back_to_menu")]
            ]
            
            message = await event.respond(groups_text, buttons=buttons)
            user_menu_state[user_id] = 'groups'
        else:
            # This is a group chat
            group_id = event.chat_id
            group_entity = await event.get_chat()
            group_name = group_entity.title
            
            # Register group if not already registered
            register_group(group_id, group_name)
            
            # Get current settings
            settings = group_settings.get(group_id, {})
            if not settings:
                settings = load_group_settings(group_id) or {}
            
            # Format settings for display
            respond_all = "✅" if settings.get('respond_to_all', False) else "❌"
            respond_mentions = "✅" if settings.get('respond_to_mentions', True) else "❌"
            respond_commands = "✅" if settings.get('respond_to_commands', True) else "❌"
            welcome_new = "✅" if settings.get('welcome_new_members', True) else "❌"
            
            groups_text = f"""
👥 **Group Settings for: {group_name}**
            
Current configuration:
• Respond to all messages: {respond_all}
• Respond to mentions: {respond_mentions}
• Respond to commands: {respond_commands}
• Welcome new members: {welcome_new}
            
What would you like to change?
            """
            
            buttons = [
                [Button.inline("🔄 Toggle Response Mode", b"toggle_group_response"),
                 Button.inline("👋 Toggle Welcome", b"toggle_welcome")],
                [Button.inline("✏️ Edit Welcome Message", b"edit_welcome_msg"),
                 Button.inline("👥 View Members", b"view_members")]
            ]
            
            await event.respond(groups_text, buttons=buttons)

    @client.on(events.NewMessage(func=lambda e: e.document or e.photo))
    async def file_handler(event):
        user_id = event.sender_id
        first_name = await get_user_name(user_id)
        
        # Check if we're awaiting a file upload
        awaiting_file = user_sessions[user_id].get('awaiting_file', False)
        
        if event.document and event.document.size > MAX_FILE_SIZE:
            await event.respond(f"Oops! That file is too big for me to handle (max: {MAX_FILE_SIZE/1024/1024}MB) 🤗")
            return
        
        # Process the file
        file_type = "document" if event.document else "photo"
        file_name = event.document.attributes[0].file_name if event.document else f"photo_{int(time.time())}.jpg"
        file_size = event.document.size if event.document else 0
        file_id = event.document.id if event.document else event.photo.id
        
        # Download file content for small files
        file_content = await download_file_content(event)
        
        # Save file info to database
        save_file_to_db(user_id, str(file_id), file_name, file_type, file_size, file_content)
        
        if awaiting_file:
            # Clear the awaiting flag
            user_sessions[user_id]['awaiting_file'] = False
            
            await event.respond(
                f"✅ File '{file_name}' uploaded successfully! It's safely stored and you can access it anytime.",
                buttons=[
                    [Button.inline("📋 View All Files", b"view_files"),
                     Button.inline("📤 Upload Another", b"upload_file")],
                    [Button.inline("◀️ Back to Menu", b"back_to_menu")]
                ]
            )
        else:
            # Regular file upload outside the upload flow
            await event.respond(f"Got your {file_type} '{file_name}', {first_name}! 📁 Safe and sound with me.")
            
            # Add a follow-up question based on file type
            if file_type == "photo":
                await asyncio.sleep(1)
                await event.respond("That's a nice image! Would you like me to describe what I see in it?")
            elif file_name.lower().endswith(('.txt', '.doc', '.docx', '.pdf')):
                await asyncio.sleep(1)
                await event.respond("Would you like me to help you analyze or summarize this document?")

    @client.on(events.ChatAction)
    async def chat_action_handler(event):
        """Handle chat actions like user joins"""
        # Check if this is a user joining a group
        if event.user_joined or event.user_added:
            # This is a group chat
            group_id = event.chat_id
            group_entity = await event.get_chat()
            group_name = group_entity.title
            
            # Register group if not already registered
            register_group(group_id, group_name)
            
            # Get settings
            if group_id not in group_settings:
                load_group_settings(group_id)
            
            settings = group_settings.get(group_id, {})
            
            # Check if we should welcome new members
            if settings.get('welcome_new_members', True):
                # Get the welcome message
                welcome_msg = settings.get('welcome_message', 
                    f"Welcome to the group! I'm {BOT_NAME}, your friendly AI assistant. Tag me or use commands to interact with me!"
                )
                
                # Get the user who joined
                user_id = event.user_id
                user = await client.get_entity(user_id)
                user_name = user.first_name
                
                # Register the user as a group member
                register_group_member(group_id, user_id, user_name)
                
                # Send welcome message
                await event.respond(f"Hey {user_name}! {welcome_msg}")

    @client.on(events.NewMessage)
    async def message_handler(event):
        user_id = event.sender_id
        
        # Ignore commands
        if event.text.startswith('/'):
            return
        
        # Check if this is a group message
        is_group = not event.is_private
        group_id = event.chat_id if is_group else None
        
        # If this is a group message, check if we should respond
        if is_group:
            # Register group if not already registered
            group_entity = await event.get_chat()
            group_name = group_entity.title
            register_group(group_id, group_name)
            
            # Register the user as a group member
            user = await client.get_entity(user_id)
            user_name = user.first_name
            register_group_member(group_id, user_id, user_name)
            
            # Check if message mentions the bot
            is_mention = False
            if event.message.entities:
                for entity in event.message.entities:
                    if isinstance(entity, types.MessageEntityMention):
                        mention_text = event.text[entity.offset:entity.offset + entity.length]
                        bot_info = await client.get_me()
                        if mention_text == f"@{bot_info.username}":
                            is_mention = True
                            break
            
            # Determine if we should respond
            if not should_respond_in_group(group_id, event.text, is_command=False, is_mention=is_mention):
                return
        
        # Check if we're awaiting a specific input
        if user_id in user_sessions:
            # Check for custom instructions
            if user_sessions[user_id].get('awaiting_custom_instructions'):
                user_sessions[user_id]['awaiting_custom_instructions'] = False
                
                # Update customization settings
                settings = get_user_customization(user_id)
                settings['custom_instructions'] = event.text
                update_user_customization(user_id, settings)
                
                success_text = """
✅ **Custom Instructions Updated**
                
Your custom instructions have been saved and will be used in our future interactions.
                """
                
                buttons = [Button.inline("◀️ Back to Customization", b"customize")]
                
                await event.respond(success_text, buttons=buttons)
                return
            
            # Check for preferred topics
            if user_sessions[user_id].get('awaiting_preferred_topics'):
                user_sessions[user_id]['awaiting_preferred_topics'] = False
                
                # Update customization settings
                settings = get_user_customization(user_id)
                settings['preferred_topics'] = event.text
                update_user_customization(user_id, settings)
                
                success_text = """
✅ **Preferred Topics Updated**
                
Your preferred topics have been saved. I'll try to focus more on these topics in our conversations.
                """
                
                buttons = [Button.inline("◀️ Back to Customization", b"customize")]
                
                await event.respond(success_text, buttons=buttons)
                return
            
            # Check for avoided topics
            if user_sessions[user_id].get('awaiting_avoided_topics'):
                user_sessions[user_id]['awaiting_avoided_topics'] = False
                
                # Update customization settings
                settings = get_user_customization(user_id)
                settings['avoided_topics'] = event.text
                update_user_customization(user_id, settings)
                
                success_text = """
✅ **Avoided Topics Updated**
                
Your avoided topics have been saved. I'll try to avoid these topics in our conversations.
                """
                
                buttons = [Button.inline("◀️ Back to Customization", b"customize")]
                
                await event.respond(success_text, buttons=buttons)
                return
            
            # Check for welcome message edit
            if user_sessions[user_id].get('awaiting_welcome_message'):
                user_sessions[user_id]['awaiting_welcome_message'] = False
                group_id = user_sessions[user_id].get('group_id')
                
                if group_id:
                    # Update the welcome message
                    update_group_settings(group_id, {'welcome_message': event.text})
                    
                    success_text = """
✅ **Welcome Message Updated**
                    
Your new welcome message has been set and will be used when new members join.
                    """
                    
                    await event.respond(success_text)
                    
                    # Refresh the group settings display
                    await groups_command_handler(event)
                
                return
            
            # Check if we're awaiting an image prompt
            if user_sessions[user_id].get('awaiting_image_prompt'):
                user_sessions[user_id]['awaiting_image_prompt'] = False
                
                # Generate the image
                await event.respond("🎨 Working on your vision... This might take a moment.")
                
                async with client.action(event.chat_id, 'upload_photo'):
                    img = await generate_image(event.text)
                    if img:
                        # Log the image generation
                        log_conversation(user_id, f"[IMAGE REQUEST] {event.text}", "[IMAGE GENERATED]", group_id=group_id)
                        
                        await client.send_file(
                            event.chat_id,
                            img,
                            caption=f"Here's your creation based on: '{event.text}' ✨",
                            buttons=Button.inline("🔄 Create Another", b"gen_image") if not is_group else None
                        )
                    else:
                        await event.respond(
                            "Sorry, I couldn't generate that image. Let's try a different description?",
                            buttons=Button.inline("🔄 Try Again", b"gen_image") if not is_group else None
                        )
                return
            
            # Check if we're awaiting a code prompt
            if user_sessions[user_id].get('awaiting_code_prompt'):
                user_sessions[user_id]['awaiting_code_prompt'] = False
                
                # Generate code
                await event.respond("💻 Crafting your code... Just a moment.")
                
                async with client.action(event.chat_id, 'typing'):
                    first_name = await get_user_name(user_id)
                    code_response = await generate_code(event.text, user_id, first_name)
                    
                    await event.respond(
                        code_response,
                        buttons=Button.inline("🔄 Generate More Code", b"gen_code") if not is_group else None
                    )
                return
            
            # Check if user is trying to access a file by number
            if user_menu_state.get(user_id) == 'view_files' and event.text.isdigit():
                file_number = int(event.text)
                files = get_user_files(user_id)
                
                if 1 <= file_number <= len(files):
                    file = files[file_number - 1]
                    file_id = file[0]
                    file_name = file[1]
                    
                    # Try to get file content from database
                    file_content, _, file_type = get_file_content(file_id)
                    
                    if file_content:
                        # We have the file content stored in the database
                        await event.respond(f"Here's your file: {file_name}")
                        
                        # Create a BytesIO object from the file content
                        file_io = BytesIO(file_content)
                        file_io.name = file_name
                        
                        # Send the file
                        await client.send_file(
                            event.chat_id,
                            file_io,
                            caption=f"File: {file_name}"
                        )
                    else:
                        # We don't have the file content, try to retrieve by file_id
                        await event.respond(f"Retrieving your file: {file_name}...")
                        
                        try:
                            # Try to send the file using the stored file_id
                            await client.send_file(
                                event.chat_id,
                                file[6],  # file_id is at index 6
                                caption=f"File: {file_name}"
                            )
                        except Exception as e:
                            logger.error(f"Error retrieving file: {e}")
                            await event.respond("Sorry, I couldn't retrieve that file. It may have expired or been deleted.")
                    
                    return
                else:
                    await event.respond("Invalid file number. Please try again.")
                    return
        
        # Regular chat message
        first_name = await get_user_name(user_id)
        
        # Update typing indicator
        async with client.action(event.chat_id, 'typing'):
            # Generate response with enhanced context
            is_code_request = "code" in event.text.lower() or "function" in event.text.lower() or "script" in event.text.lower()
            response_text, context_used = await generate_ai_response(event.text, user_id, first_name, is_code_request=is_code_request)
            
            # Log the conversation with context tracking
            message_number = log_conversation(user_id, event.text, response_text, context_used, group_id=group_id)
            
            # Send the response
            await event.respond(response_text)

    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        keep_alive()  # Start the Flask server
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(f"{BOT_NAME} stopped peacefully")
    except Exception as e:
        logger.error(f"💔 Critical error: {e}")
