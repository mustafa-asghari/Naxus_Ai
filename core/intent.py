from __future__ import annotations

from enum import Enum


class Intent(Enum):
    UNKNOWN = "UNKNOWN"

    # App control
    OPEN_APP = "OPEN_APP"
    CLOSE_APP = "CLOSE_APP"
    CLOSE_ALL_APPS = "CLOSE_ALL_APPS"

    # Utilities
    SEARCH_WEB = "SEARCH_WEB"
    CREATE_NOTE = "CREATE_NOTE"
    OPEN_URL = "OPEN_URL"
    CONTACTS = "CONTACTS"
    MAIL = "MAIL"
    REMINDERS = "REMINDERS"
    CALENDAR = "CALENDAR"
    MAPS = "MAPS"

    # Communication
    SEND_MESSAGE = "SEND_MESSAGE"
    TYPE_TEXT = "TYPE_TEXT"
    READ_MESSAGES = "READ_MESSAGES"
    READ_SCREEN = "READ_SCREEN"

    # Nexus control
    EXIT = "EXIT"                    # Go to sleep mode
    STOP_NEXUS = "STOP_NEXUS"        # Terminate Nexus
    RESTART_NEXUS = "RESTART_NEXUS"  # Restart Nexus
