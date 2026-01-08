import rumps
import subprocess
import signal
import os
import sys
import time


class NexusApp(rumps.App):
    def __init__(self):
        super(NexusApp, self).__init__("Nexus", title="🤖")
        self.process = None
        self.nexus_pid = None
        self.menu = ["Restart Nexus", "Quit Nexus"]
        self.start_nexus()

    def start_nexus(self):
        """Starts Nexus from the parent directory."""
        launcher_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.dirname(launcher_dir)
        nexus_path = os.path.join(root_dir, "nexus.py")

        print(f"Starting Nexus from: {root_dir}")

        self.process = subprocess.Popen(
            [sys.executable, nexus_path],
            cwd=root_dir,
            preexec_fn=os.setsid,
            close_fds=True
        )
        self.nexus_pid = self.process.pid
        print(f"Nexus started with PID: {self.nexus_pid}")

    def stop_nexus(self):
        """Stops the Nexus subprocess and ALL related processes."""
        print("Stopping Nexus...")

        # Method 1: Kill by stored PID and process group
        if self.process and self.nexus_pid:
            try:
                pgid = os.getpgid(self.nexus_pid)
                os.killpg(pgid, signal.SIGTERM)
                time.sleep(0.3)
                os.killpg(pgid, signal.SIGKILL)
                print(f"Killed process group {pgid}")
            except (ProcessLookupError, OSError) as e:
                print(f"Process group kill: {e}")

        # Method 2: Kill the subprocess directly
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=1)
            except:
                try:
                    self.process.kill()
                except:
                    pass
            self.process = None

        # Method 3: Use shell commands to kill ALL nexus-related processes
        # This is the nuclear option - catches anything that escaped
        kill_commands = [
            "pkill -9 -f 'python.*nexus.py'",
            "pkill -9 -f 'python.*mcp_server.py'",
            "pkill -9 -f 'pvporcupine'",  # Wake word engine
        ]

        for cmd in kill_commands:
            try:
                subprocess.run(cmd, shell=True, capture_output=True, timeout=2)
            except:
                pass

        self.nexus_pid = None
        print("Nexus stopped.")

    @rumps.clicked("Restart Nexus")
    def restart(self, _):
        self.stop_nexus()
        time.sleep(1)
        self.start_nexus()

    @rumps.clicked("Quit Nexus")
    def quit(self, _):
        self.stop_nexus()
        rumps.quit_application()


if __name__ == "__main__":
    NexusApp().run()