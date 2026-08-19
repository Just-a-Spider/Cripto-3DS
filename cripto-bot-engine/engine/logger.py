import logging
import collections

recent_logs = collections.deque(maxlen=50)

class MemoryLogHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        recent_logs.append(msg)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("CriptoBotEngine")
mem_handler = MemoryLogHandler()
mem_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(mem_handler)

# Capture Discord.py gateway and connection logs in Web UI
discord_logger = logging.getLogger("discord")
discord_logger.setLevel(logging.INFO)
discord_logger.addHandler(mem_handler)
