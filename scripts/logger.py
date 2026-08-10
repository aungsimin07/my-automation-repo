class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    CYAN = '\033[96m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'

class Log:
    @staticmethod
    def start(msg): print(f"\n{Colors.BOLD}{Colors.CYAN}[=== START ===]{Colors.RESET} {msg}")
    @staticmethod
    def http(msg): print(f"\n   {Colors.BLUE}[HTTP]{Colors.RESET} {msg}")
    @staticmethod
    def info(msg): print(f"   {Colors.CYAN}[INFO]{Colors.RESET} {msg}")
    @staticmethod
    def ok(msg): print(f"   {Colors.GREEN}[OK]{Colors.RESET} {msg}")
    @staticmethod
    def error(msg): print(f"   {Colors.RED}[ERROR]{Colors.RESET} {msg}")
    @staticmethod
    def section_in(msg): print(f"\n{Colors.MAGENTA}[>>>]{Colors.RESET} {msg}")
    @staticmethod
    def section_out(msg): print(f"   {Colors.YELLOW}[|||]{Colors.RESET} {msg}")
    @staticmethod
    def scan(msg): print(f"   {Colors.CYAN}[SCAN]{Colors.RESET} {msg}")
    @staticmethod
    def warn(msg): print(f"      └─ {Colors.YELLOW}[WARN]{Colors.RESET} {msg}")
    @staticmethod
    def skip(msg): print(f"      └─ {Colors.YELLOW}[SKIP]{Colors.RESET} {msg}")
    @staticmethod
    def dupe(msg): print(f"      └─ {Colors.YELLOW}[DUPE]{Colors.RESET} {msg}")
    @staticmethod
    def match(msg): print(f"      └─ {Colors.GREEN}[MATCH]{Colors.RESET} {msg}")
    @staticmethod
    def api(msg): print(f"   {Colors.BLUE}[API]{Colors.RESET} {msg}")
    @staticmethod
    def fetch(msg): print(f"\n{Colors.CYAN}[FETCH]{Colors.RESET} {msg}")
    @staticmethod
    def success(msg): print(f"\n{Colors.BOLD}{Colors.GREEN}[SUCCESS]{Colors.RESET} {msg}")
    @staticmethod
    def done(msg): print(f"\n{Colors.BOLD}{Colors.YELLOW}[DONE]{Colors.RESET} {msg}")
