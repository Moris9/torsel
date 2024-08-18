import os
import subprocess
import time
import socket
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from stem import Signal
from stem.control import Controller
import threading
import shutil
from queue import Queue

class Torsel:
    """
    Torsel: A Python module for managing Tor instances with Selenium.

    This class provides functionality to create, manage, and rotate multiple Tor instances,
    as well as configure Selenium WebDriver to use these instances for web automation.
    """

    def __init__(self, total_instances=10, max_threads=5, tor_base_port=9050, tor_control_base_port=9151, tor_path="/usr/bin/tor", tor_data_dir="/tmp/tor_profiles", headless=True, verbose=True):
        """
        Initializes the Torsel object with the given parameters.

        Args:
            total_instances (int): Number of Tor instances to create.
            max_threads (int): Maximum number of concurrent threads.
            tor_base_port (int): Base port number for Tor SOCKS connections.
            tor_control_base_port (int): Base port number for Tor control connections.
            tor_path (str): Path to the Tor executable.
            tor_data_dir (str): Directory to store Tor profiles.
            headless (bool): Run Selenium in headless mode if True.
            verbose (bool): If True, print logs to the console.
        """
        self.total_instances = total_instances
        self.max_threads = max_threads
        self.tor_base_port = tor_base_port
        self.tor_control_base_port = tor_control_base_port
        self.tor_path = tor_path
        self.tor_data_dir = tor_data_dir
        self.headless = headless
        self.verbose = verbose

    def log(self, message):
        """
        Logs a message to the console if verbose mode is enabled.

        Args:
            message (str): The message to log.
        """
        if self.verbose:
            print(message)

    def clean_up(self):
        """
        Cleans up any previous Tor processes, files, and ports.
        Kills any running Tor processes, frees up occupied ports, and removes old Tor profile directories.
        """
        self.log("[~] Cleaning up previous processes, files, and ports...")
        subprocess.call(['killall', 'tor'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)

        for port in range(self.tor_base_port, self.tor_base_port + self.total_instances * 10, 10):
            if self.is_port_open(port):
                os.system(f"fuser -k {port}/tcp")
            if self.is_port_open(port + 101):
                os.system(f"fuser -k {port + 101}/tcp")

        if os.path.exists(self.tor_data_dir):
            shutil.rmtree(self.tor_data_dir)

        self.log("[+] Cleanup completed.")
        time.sleep(3)

    def create_tor_instance(self, instance_num):
        """
        Creates and configures a Tor instance with the specified instance number.

        Args:
            instance_num (int): The index of the Tor instance.
        """
        self.log(f"[~] Creating Tor instance {instance_num}...")
        instance_dir = os.path.join(self.tor_data_dir, f"tor{instance_num}")
        os.makedirs(instance_dir, exist_ok=True)

        torrc_content = f'''
SocksPort {self.tor_base_port + instance_num * 10}
ControlPort {self.tor_control_base_port + instance_num * 10}
DataDirectory {instance_dir}
'''
        torrc_path = os.path.join(instance_dir, "torrc")
        with open(torrc_path, 'w') as torrc_file:
            torrc_file.write(torrc_content)

        subprocess.Popen([self.tor_path, "-f", torrc_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5)
        self.log(f"[+] Tor instance {instance_num} created and running.")

    def configure_selenium_with_tor(self, instance_num):
        """
        Configures Selenium WebDriver to use a Tor instance as a proxy.

        Args:
            instance_num (int): The index of the Tor instance.

        Returns:
            WebDriver: Configured Selenium WebDriver instance.
        """
        chrome_options = Options()
        if self.headless:
            chrome_options.add_argument("--headless")
        chrome_options.add_argument(f"--proxy-server=socks5://127.0.0.1:{self.tor_base_port + instance_num * 10}")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")

        service = Service()
        driver = webdriver.Chrome(service=service, options=chrome_options)
        return driver

    def rotate_tor_ip(self, instance_num):
        """
        Rotates the IP address of a Tor instance by sending the NEWNYM signal.

        Args:
            instance_num (int): The index of the Tor instance.
        """
        control_port = self.tor_control_base_port + instance_num * 10
        if self.is_port_open(control_port):
            with Controller.from_port(port=control_port) as controller:
                controller.authenticate()
                controller.signal(Signal.NEWNYM)
            time.sleep(5)
            self.log(f"[+] IP rotated for Tor instance {instance_num}.")
        else:
            self.log(f"[-] Control port {control_port} not accessible for instance {instance_num}.")

    def is_port_open(self, port):
        """
        Checks if a specific port is open.

        Args:
            port (int): The port number to check.

        Returns:
            bool: True if the port is open, False otherwise.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            result = sock.connect_ex(('127.0.0.1', port))
            return result == 0

    def execute_function(self, action_num, instance_num, user_function):
        """
        Executes the user-provided function with the specified Tor instance.

        Args:
            action_num (int): The action number being performed.
            instance_num (int): The index of the Tor instance.
            user_function (callable): The function to execute, provided by the user.
        """
        driver = self.configure_selenium_with_tor(instance_num)
        user_function(driver, action_num, instance_num, self.log)
        driver.quit()

    def thread_manager(self, queue, user_function, check_stop_func=None):
        """
        Manages the execution of threads, ensuring that actions are processed concurrently.

        Args:
            queue (Queue): The queue containing action numbers.
            user_function (callable): The function to execute for each action.
            check_stop_func (callable, optional): A function to check if execution should stop.
        """
        while not queue.empty():
            action_num = queue.get()
            instance_num = action_num % self.total_instances  # Rotate between available instances

            if action_num < self.total_instances:
                self.create_tor_instance(instance_num)
                time.sleep(2)

            for attempt in range(5):
                try:
                    self.execute_function(action_num, instance_num, user_function)
                    break
                except Exception as e:
                    self.log(f"[-] Action {action_num}, Instance {instance_num} - Exception: {e}. Rotating IP and retrying...")
                    self.rotate_tor_ip(instance_num)
            queue.task_done()

            if check_stop_func and check_stop_func():
                while not queue.empty():
                    queue.get_nowait()
                    queue.task_done()
                break

    def run(self, num_actions, user_function, check_stop_func=None):
        """
        Runs the specified number of actions concurrently across the available Tor instances.

        Args:
            num_actions (int): The number of actions to perform.
            user_function (callable): The function to execute for each action.
            check_stop_func (callable, optional): A function to check if execution should stop.
        """
        self.clean_up()

        queue = Queue()
        for i in range(num_actions):
            queue.put(i)

        threads = []
        for _ in range(min(num_actions, self.max_threads)):
            t = threading.Thread(target=self.thread_manager, args=(queue, user_function, check_stop_func))
            t.start()
            threads.append(t)

        for t in threads:
            t.join()