import time
import requests

url = "http://127.0.0.1:5000"
timeout = 10  # Sekunden warten
start_time = time.time()

while time.time() - start_time < timeout:
    try:
        requests.get(url)
        print("Server is up!")
        import os
        os.startfile(url)  # Öffnet den Standardbrowser
        break
    except requests.ConnectionError:
        time.sleep(1)
else:
    print("Server did not start within timeout period.")