import json
import os
import uuid
from datetime import datetime

class SimpleDB:
    def __init__(self, filename='database.json'):
        self.filename = filename
        self.load()

    def load(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r') as f:
                    self.data = json.load(f)
            except json.JSONDecodeError:
                self.data = {}
        else:
            self.data = {}

    def save(self):
        with open(self.filename, 'w') as f:
            json.dump(self.data, f, indent=4)

    def insert(self, collection, item):
        if collection not in self.data:
            self.data[collection] = []
        
        # Add metadata
        item['_id'] = str(uuid.uuid4())
        item['created_at'] = datetime.utcnow().isoformat()
        
        self.data[collection].append(item)
        self.save()
        return item['_id']

    def get_all(self, collection):
        return self.data.get(collection, [])

    def find_one(self, collection, query):
        """
        Finds the first item in 'collection' where key/values match 'query' dict.
        """
        if collection not in self.data: return None
        
        for item in self.data[collection]:
            match = True
            for k, v in query.items():
                if item.get(k) != v:
                    match = False
                    break
            if match:
                return item
        return None

    def update(self, collection, query, new_values):
        """
        Updates the first item matching 'query' with 'new_values'.
        """
        item = self.find_one(collection, query)
        if item:
            for k, v in new_values.items():
                item[k] = v
            item['updated_at'] = datetime.utcnow().isoformat()
            self.save()
            return True
        return False

# Singleton instance
db = SimpleDB()
