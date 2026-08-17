import sys

class Logger:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @staticmethod
    def info(message: str) -> None:
        print(f"{Logger.CYAN}[i] INFO:{Logger.RESET} {message}")

    @staticmethod
    def success(message: str) -> None:
        print(f"{Logger.GREEN}[✓] SUCCESS:{Logger.RESET} {message}")

    @staticmethod
    def warning(message: str) -> None:
        print(f"{Logger.YELLOW}[!] WARNING:{Logger.RESET} {message}")

    @staticmethod
    def error(message: str, fatal: bool = False) -> None:
        print(f"{Logger.RED}[✗] ERROR:{Logger.RESET} {message}", file=sys.stderr)
        if fatal:
            sys.exit(1)
