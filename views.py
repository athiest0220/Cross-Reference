from django.shortcuts import render
from sqlalchemy import create_engine, text
from fuzzywuzzy import fuzz
from django.http import JsonResponse
from django.core.signals import request_finished
import time
import threading
from queue import Queue
import logging
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from django.core.cache import cache
from fuzzywuzzy import fuzz
#from .your_module import perform_search  # Adjust the import path as needed

# Database connection string
server = '10.180.3.35'
database = 'JouleJar'
username = 'Tgallagher'
password = '43MiVw8ei6bc'
table = 'dbo.Cross_Reference'
connection_string = f'mssql+pyodbc://{username}:{password}@{server}/{database}?driver=ODBC+Driver+17+for+SQL+Server'
engine = create_engine(connection_string)

# Global variables
status_lock = threading.Lock()
result_queue = Queue()
logging.basicConfig(level=logging.DEBUG)
executor = ThreadPoolExecutor(max_workers=5)
session_resources = {}

def process_request(resources, request):
    with resources['lock']:
        pass

def shutdown():
    executor.shutdown(wait=True)

def get_session_resources(session_id):
    if session_id not in session_resources:
        session_resources[session_id] = {
            'queue': Queue(),
            'lock': Lock()
        }
    return session_resources[session_id]

def handle_request(request):
    session_id = request.session.session_key
    resources = get_session_resources(session_id)
    future = executor.submit(process_request, resources, request)

    try:
        result = future.result(timeout=10)
    except TimeoutError:
        result = "Request timed out"
    except Exception as e:
        result = f"An error occurred: {str(e)}"
    return result

def perform_search(part_number):
    with engine.connect() as connection:
        # First, try exact match
        query = text(f"SELECT * FROM {table} WHERE PartNumber = :part_number")
        result = connection.execute(query, {'part_number': part_number}).fetchone()
        
        if result:
            return {
                'input_part_number': part_number,
                'match_type': 'Exact',
                'score': 100,
                'result': dict(zip(result._fields, result))
            }
        
        # If no exact match, get all part numbers for fuzzy matching
        all_parts_query = text(f"SELECT PartNumber FROM {table}")
        all_parts = [row[0] for row in connection.execute(all_parts_query)]
        
        # Check for transposed characters
        transposed = [p for p in all_parts if len(p) == len(part_number) and set(p) == set(part_number)]
        if transposed:
            best_transposed = max(transposed, key=lambda x: fuzz.ratio(x, part_number))
            query = text(f"SELECT * FROM {table} WHERE PartNumber = :part_number")
            result = connection.execute(query, {'part_number': best_transposed}).fetchone()
            return {
                'input_part_number': part_number,
                'match_type': 'Transposed',
                'score': fuzz.ratio(best_transposed, part_number),
                'result': dict(zip(result._fields, result)) if result else None
            }
        
        # If no transposed match, use fuzzy logic
        best_match = max(all_parts, key=lambda x: fuzz.ratio(x, part_number))
        score = fuzz.ratio(best_match, part_number)
        if score > 80:  # You can adjust this threshold
            query = text(f"SELECT * FROM {table} WHERE PartNumber = :part_number")
            result = connection.execute(query, {'part_number': best_match}).fetchone()
            return {
                'input_part_number': part_number,
                'match_type': 'Fuzzy',
                'score': score,
                'result': dict(zip(result._fields, result)) if result else None
            }
        
        # If no good match found
        return {
            'input_part_number': part_number,
            'match_type': 'No Match',
            'score': 0,
            'result': None
        }

def search_parts(request):
    if request.method == 'POST':
        part_numbers = request.POST.get('part_numbers', '').split()

        # Use cache instead of session for storing queue and lock
        cache_key = f"search_results_{request.session.session_key}"
        cache.set(cache_key, [], 3600)  # Store an empty list initially, expires in 1 hour

        thread = Thread(target=process_search, args=(part_numbers, cache_key))
        thread.start()

        # Get search status
        status = get_search_status(cache_key)

        return render(request, 'crossref_search/index.html', {'status': status})

    # For GET requests
    cache_key = f"search_results_{request.session.session_key}"
    results = cache.get(cache_key, [])

    return render(request, 'crossref_search/index.html', {'results': results})
    
def get_search_status(cache_key):
    results = cache.get(cache_key, [])
    if not results:
        return {
            'total_parts': 0,
            'current_part': 0,
            'status_message': 'Waiting to start...',
            'detail_message': '',
            'results': []
        }
    
    latest = results[-1]
    return {
        'total_parts': latest['total_parts'],
        'current_part': latest['current_part'],
        'status_message': latest['status_message'],
        'detail_message': latest['detail_message'],
        'results': [r['result'] for r in results if r['result']]
    }

def process_search(part_numbers, cache_key):
    total_parts = len(part_numbers)
    print(f"Starting search for {total_parts} parts")  # Debug print

    for i, pn in enumerate(part_numbers):
        current_part = i + 1
        status_message = f"Processing part number {current_part} of {total_parts}: {pn}..."
        detail_message = f"Initializing search for {pn}..."
        
        print(status_message)  # Debug print
        
        try:
            result = perform_search(pn)
        except Exception as e:
            print(f"Error processing {pn}: {str(e)}")
            result = {
                'input_part_number': pn,
                'match_type': 'Error',
                'score': 0,
                'result': None
            }

        results = cache.get(cache_key, [])
        results.append({
            'total_parts': total_parts,
            'current_part': current_part,
            'status_message': status_message,
            'detail_message': detail_message,
            'result': result
        })
        cache.set(cache_key, results, 3600)
        print(f"Updated cache: {results}")  # Debug print

        time.sleep(1)  # Reduced sleep time for faster processing

    print("Search completed")  # Debug print
    results = cache.get(cache_key, [])
    results.append({
        'total_parts': total_parts,
        'current_part': total_parts,
        'status_message': "Search complete.",
        'detail_message': "All parts processed successfully.",
        'result': None
    })
    cache.set(cache_key, results, 3600)
    print(f"Final cache: {results}")  # Debug print

def cleanup_session_resources(session_id):
    if session_id in session_resources:
        del session_resources[session_id]
        
def cleanup_after_request(sender, **kwargs):
    session = kwargs.get('session', None)  # Retrieve the session if available
    if session:  # Check if a session object was provided
        session_id = session.session_key
        cleanup_session_resources(session_id)

def get_status(request):
    cache_key = f"search_results_{request.session.session_key}"
    results = cache.get(cache_key, [])
    print(f"Retrieved from cache: {results}")

    if not results:
        status = {
            'total_parts': 0,
            'current_part': 0,
            'status_message': 'Waiting to start...',
            'detail_message': '',
            'results': []
        }
    else:
        latest = results[-1]
        status = {
            'total_parts': latest['total_parts'],
            'current_part': latest['current_part'],
            'status_message': latest['status_message'],
            'detail_message': latest['detail_message'],
            'results': [r['result'] for r in results if r['result'] is not None]
        }

    print(f"Status to be returned: {status}")
    return JsonResponse(status, safe=False)
    
# Connect the request_finished signal to the cleanup_after_request function
request_finished.connect(cleanup_after_request)