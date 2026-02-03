"""
This module contains the FirebaseObject class that is used to interact with the firebase services
"""
from typing import Union

from firebase_admin import (
    _CONFIG_VALID_KEYS,
    _DEFAULT_APP_NAME,
    App,
    credentials,
    db,
    firestore,
    storage,
    get_app,
    initialize_app,
)
from google.cloud.storage import Blob


class FirebaseObject:
    """This class is used to interact with the firebase services"""

    real_time_db: Union[db.Reference, None] = None

    def __init__(self, application: App, options: dict = None):
        self.app_name = application.name
        self.app = application
        self.fs_client = firestore.client(app=application)
        self.options = options
        

        if self.app.options.get("databaseURL"):
            self.real_time_db = db.reference(app=application)

    @staticmethod
    def _validate_config(config: dict):
        for key in config.keys():
            if key not in _CONFIG_VALID_KEYS:
                raise ValueError(f"Invalid config key: {key}")

    @classmethod
    def from_json(
        cls,
        service_account_path: str,
        app_name: Union[str, None] = _DEFAULT_APP_NAME,
        options: dict = None,
    ):
        """Classmethod to instantiate a class that initializes a firebase from a json file

        Args:
            service_account_path (str): The path to the json file that contains the service account
            app_name (Union[str, None], optional): The name of the given app. Defaults to _DEFAULT_APP_NAME (firebase-admin)
            options (dict, optional): Additional config options. Defaults to None.

        Raises:
            TypeError: If options is not a dictionary

        Returns:
            app (App): The firebase App Object to interact with
        """
        ## Classmethod to instantiate a class that initializes a firebase from a json file
        cred = credentials.Certificate(cert=service_account_path)
        try:
            if options:
                if not isinstance(options, dict):
                    raise TypeError("Options must be a dictionary")
                app = initialize_app(credential=cred, name=app_name, options=options)
            else:
                app = initialize_app(credential=cred, name=app_name)
        except ValueError:
            # If the app already exists, just get it
            app = get_app(app_name)

        return cls(application=app,options=options)



    @property
    def storage_client(self):
        if self.options and "storageBucket" in self.options:
            return storage.bucket(app=self.app)
            
    
    def add_real_time_db(self, url: str):
        """Add a real time dabatabase to the app instance by providing a url

        Args:
            url (str): The base url of the real time database

        Raises:
            ValueError: If the database already exists
        """
        ## Add a database url to the app
        if not self.real_time_db:
            self.real_time_db = db.reference(app=self.app, url=url)
        else:
            raise ValueError("Database already exists")
        
    def delete_collection(self,coll_ref, batch_size=100):
            docs = coll_ref.list_documents(page_size=batch_size)
            deleted = 0

            for doc in docs:
                doc.delete()
                deleted = deleted + 1

            if deleted >= batch_size:
                return self.delete_collection(coll_ref, batch_size)
    