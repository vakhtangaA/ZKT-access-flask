from queue import Queue
from threading import Thread
from device_locks import output_lock, with_device_lock
from main import add_user as add_user_func
from main import delete_user as delete_user_func
from main import get_users as get_users_func

# Define the queue and lock for thread safety
request_queue = Queue()

# Function to process requests from the queue
def process_requests():
    while True:
        try:
            request = request_queue.get()
            if request is None:
                break  # Exit thread if None is received from the queue
            card, pin, ip, port, operation = request
            if operation == 'add':
                add_user(card, pin, ip, port)
            elif operation == 'delete':
                delete_user(card, pin, ip, port)
            elif operation == 'list':
                get_users(ip, port)
            request_queue.task_done()
        except Exception as e:
            with output_lock:
                with open('output.txt', 'a') as output:
                    output.write(f"An error occurred: {str(e)}" + "\n")

# Function to add a request to the queue
def add_request(card, pin, ip, port, operation):
    request_queue.put((card, pin, ip, port, operation))

# Function to handle adding a user
def add_user(card, pin, ip, port, doors=None, timeout=4000, password='', model=None):
    return with_device_lock(
        ip,
        port,
        lambda: add_user_func(
            card,
            pin,
            ip,
            port,
            doors=doors,
            timeout=timeout,
            password=password,
            model=model,
        ),
    )

# Function to handle deleting a user
def delete_user(card, pin, ip, port, timeout=4000, password='', model=None):
    return with_device_lock(
        ip,
        port,
        lambda: delete_user_func(
            card,
            pin,
            ip,
            port,
            timeout=timeout,
            password=password,
            model=model,
        ),
    )

# Function to handle getting users
def get_users(ip, port, timeout=10000, password='', model=None):
    return with_device_lock(
        ip,
        port,
        lambda: get_users_func(
            ip,
            port,
            timeout=timeout,
            password=password,
            model=model,
        ),
    )

# Start the thread to process requests
thread = Thread(target=process_requests)
thread.start()
