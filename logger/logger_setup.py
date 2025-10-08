import logging
import os
import inspect


class LoggerSetup:
    def __init__(self, log_filename: str):
        os.makedirs("logs", exist_ok=True)
        log_path = os.path.join("logs", log_filename)

        self.logger = logging.getLogger(log_filename)
        self.logger.setLevel(logging.INFO)

        if not self.logger.handlers:
            # 👇 use %(caller_file)s instead of %(filename)s
            formatter = logging.Formatter(
                "%(asctime)s | %(levelname)s | %(caller_file)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )

            file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
            file_handler.setFormatter(formatter)

            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)

            self.logger.addHandler(file_handler)
            self.logger.addHandler(console_handler)

    def get_logger(self):
        return self.CustomLogger(self.logger)

    class CustomLogger:
        def __init__(self, base_logger):
            self.base_logger = base_logger

        def _log(self, level, msg, *args, **kwargs):
            # ✅ Get caller file dynamically
            frame = inspect.stack()[2]
            filename = os.path.basename(frame.filename)

            # ✅ Use custom key 'caller_file'
            extra = {'caller_file': filename}
            self.base_logger._log(level, msg, args, **kwargs, extra=extra)

        def info(self, msg, *args, **kwargs):
            self._log(logging.INFO, msg, *args, **kwargs)

        def warning(self, msg, *args, **kwargs):
            self._log(logging.WARNING, msg, *args, **kwargs)

        def error(self, msg, *args, **kwargs):
            self._log(logging.ERROR, msg, *args, **kwargs)

        def debug(self, msg, *args, **kwargs):
            self._log(logging.DEBUG, msg, *args, **kwargs)

