import sys
from logging import Logger
from typing import List
import time

import requests
from requests.models import Response

from DatabaseHelper import Database
from Exceptions import MonicaFetchError, InternalError


class Monica():
    '''Handles all Monica related (api) stuff.'''

    def __init__(self, log: Logger, database_handler: Database, token: str, base_url: str, create_reminders: bool, label_filter: dict) -> None:
        self.log = log
        self.database = database_handler
        self.base_url = base_url
        self.label_filter = label_filter
        self.header = {'Authorization': f'Bearer {token}'}
        self.parameters = {'limit': 100}
        self.is_data_already_fetched = False
        self.contacts = []
        self.gender_mapping = {}
        self.contact_field_type_mapping = {}
        self.updated_contacts = {}
        self.created_contacts = {}
        self.deleted_contacts = {}
        self.api_requests = 0
        self.create_reminders = create_reminders

    def __filter_contacts_by_label(self, contact_list: List[dict]) -> List[dict]:
        '''Filters a contact list by include/exclude labels.'''
        if self.label_filter["include"]:
            return [contact for contact in contact_list
                    if any([contact_label["name"]
                            in self.label_filter["include"] 
                            for contact_label in contact["tags"]])
                    and all([contact_label["name"] 
                            not in self.label_filter["exclude"] 
                            for contact_label in contact["tags"]])]
        elif self.label_filter["exclude"]:
            return [contact for contact in contact_list
                    if all([contact_label["name"]
                            not in self.label_filter["exclude"] 
                            for contact_label in contact["tags"]])]
        else:
            return contact_list

    def update_statistics(self) -> None:
        '''Updates internal statistics for printing.'''
        # A contact should only count as updated if it has not been created during sync
        self.updated_contacts = {key: value for key, value in self.updated_contacts.items()
                                if key not in self.created_contacts}

    def get_gender_mapping(self) -> dict:
        '''Fetches all genders from Monica and saves them to a dictionary.'''
        # Only fetch if not present yet
        if self.gender_mapping:
            return self.gender_mapping

        while True:
        # Get genders
            response = requests.get(
                self.base_url + f"/genders", headers=self.header, params=self.parameters)
            self.api_requests += 1

            # If successful
            if response.status_code == 200:
                genders = response.json()['data']
                gender_mapping = {gender['type']: gender['id'] for gender in genders}
                self.gender_mapping = gender_mapping
                return self.gender_mapping
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                self.log.error(f"Failed to fetch genders from Monica: {error}")
                raise MonicaFetchError("Error fetching genders from Monica!")

    def update_contact(self, monica_id: str, data: dict) -> None:
        '''Updates a given contact and its id via api call.'''
        name = f"{data['first_name']} {data['last_name']}"

        # Remove Monica contact from contact list (add again after updated)
        self.contacts = [c for c in self.contacts if str(c['id']) != str(monica_id)]

        while True:
            # Update contact
            response = requests.put(self.base_url + f"/contacts/{monica_id}", headers=self.header, params=self.parameters, json=data)
            self.api_requests += 1

            # If successful
            if response.status_code == 200:
                contact = response.json()['data']
                self.updated_contacts[monica_id] = True
                self.contacts.append(contact)
                name = contact["complete_name"]
                self.log.info(f"'{name}' ('{monica_id}'): Contact updated successfully")
                self.database.update(
                    monica_id=monica_id, monica_last_changed=contact['updated_at'], monica_full_name=contact["complete_name"])
                return
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                self.log.error(f"'{name}' ('{monica_id}'): Error updating Monica contact: {error}. Does it exist?")
                self.log.error(f"Monica form data: {data}")
                raise MonicaFetchError("Error updating Monica contact!")

    def delete_contact(self, monica_id: str, name: str) -> None:
        '''Deletes the contact with the given id from Monica and removes it from the internal list.'''

        while True:
            # Delete contact
            response = requests.delete(
                self.base_url + f"/contacts/{monica_id}", headers=self.header, params=self.parameters)
            self.api_requests += 1

            # If successful
            if response.status_code == 200:
                self.contacts = [c for c in self.contacts if str(c['id']) != str(monica_id)]
                self.deleted_contacts[monica_id] = True
                return
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                self.log.error(f"'{name}' ('{monica_id}'): Failed to complete delete request: {error}")
                raise MonicaFetchError("Error deleting Monica contact!")

    def create_contact(self, data: dict, reference_id: str) -> dict:
        '''Creates a given Monica contact via api call and returns the created contact.'''
        while True:
            # Create contact
            response = requests.post(self.base_url + f"/contacts",
                                    headers=self.header, params=self.parameters, json=data)
            self.api_requests += 1

            # If successful
            if response.status_code == 201:
                contact = response.json()['data']
                self.created_contacts[contact['id']] = True
                self.contacts.append(contact)
                self.log.info(f"'{reference_id}' ('{contact['id']}'): Contact created successfully")
                return contact
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                self.log.info(f"'{reference_id}': Error creating Monica contact: {error}")
                raise MonicaFetchError("Error creating Monica contact!")

    def get_contacts(self) -> List[dict]:
        '''Fetches all contacts from Monica if not already fetched.'''
        try:
            # Avoid multiple fetches
            if self.is_data_already_fetched:
                return self.contacts

            # Start fetching
            max_page = '?'
            page = 1
            contacts = []
            self.log.info("Fetching all Monica contacts...")
            while True:
                sys.stdout.write(f"\rFetching all Monica contacts (page {page} of {max_page})")
                sys.stdout.flush()
                response = requests.get(
                    self.base_url + f"/contacts?page={page}", headers=self.header, params=self.parameters)
                self.api_requests += 1
                # If successful
                if response.status_code == 200:
                    data = response.json()
                    contacts += data['data']
                    max_page = data['meta']['last_page']
                    if page == max_page:
                        self.contacts = self.__filter_contacts_by_label(contacts)
                        break
                    page += 1
                else:
                    error = response.json()['error']['message']
                    if self.__is_slow_down_error(response, error):
                        continue
                    msg = f"Error fetching Monica contacts: {error}"
                    self.log.error(msg)
                    raise MonicaFetchError(msg)
            self.is_data_already_fetched = True
            msg = "Finished fetching Monica contacts"
            self.log.info(msg)
            print("\n" + msg)
            return self.contacts

        except Exception as e:
            msg = f"Failed to fetch Monica contacts (maybe connection issue): {str(e)}"
            print("\n" + msg)
            self.log.error(msg)
            raise MonicaFetchError(msg) from e

    def get_contact(self, monica_id: str) -> dict:
        '''Fetches a single contact by id from Monica.'''
        try:
            # Check if contact is already fetched
            if self.contacts:
                monica_contact_list = [c for c in self.contacts if str(c['id']) == str(monica_id)]
                if monica_contact_list: 
                    return monica_contact_list[0]

            while True:
                # Fetch contact
                response = requests.get(
                    self.base_url + f"/contacts/{monica_id}", headers=self.header, params=self.parameters)
                self.api_requests += 1

                # If successful
                if response.status_code == 200:
                    monica_contact = response.json()['data']
                    monica_contact = self.__filter_contacts_by_label([monica_contact])[0]
                    self.contacts.append(monica_contact)
                    return monica_contact
                else:
                    error = response.json()['error']['message']
                    if self.__is_slow_down_error(response, error):
                        continue
                    raise MonicaFetchError(error)

        except IndexError as e:
            msg = f"Contact processing of '{monica_id}' not allowed by label filter"
            self.log.info(msg)
            raise InternalError(msg) from e

        except Exception as e:
            msg = f"Failed to fetch Monica contact '{monica_id}': {str(e)}"
            self.log.error(msg)
            raise MonicaFetchError(msg) from e

    def get_notes(self, monica_id: str, name: str) -> List[dict]:
        '''Fetches all contact notes for a given Monica contact id via api call.'''

        while True:
            # Get contact fields
            response = requests.get(self.base_url + f"/contacts/{monica_id}/notes", headers=self.header, params=self.parameters)
            self.api_requests += 1

            # If successful
            if response.status_code == 200:
                monica_notes = response.json()['data']
                return monica_notes
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                raise MonicaFetchError(f"'{name}' ('{monica_id}'): Error fetching Monica notes: {error}")

    def add_note(self, data: dict, name: str) -> None:
        '''Creates a new note for a given contact id via api call.'''
        # Initialization
        monica_id = data['contact_id']

        while True:
            # Create address
            response = requests.post(self.base_url + f"/notes", headers=self.header, params=self.parameters, json=data)
            self.api_requests += 1

            # If successful
            if response.status_code == 201:
                self.updated_contacts[monica_id] = True
                note = response.json()['data']
                note_id = note["id"]
                self.log.info(f"'{name}' ('{monica_id}'): Note '{note_id}' created successfully")
                return
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                raise MonicaFetchError(f"'{name}' ('{monica_id}'): Error creating Monica note: {error}")

    def update_note(self, note_id: str, data: dict, name: str) -> None:
        '''Creates a new note for a given contact id via api call.'''
        # Initialization
        monica_id = data['contact_id']

        while True:
            # Create address
            response = requests.put(self.base_url + f"/notes/{note_id}", headers=self.header, params=self.parameters, json=data)
            self.api_requests += 1

            # If successful
            if response.status_code == 200:
                self.updated_contacts[monica_id] = True
                note = response.json()['data']
                note_id = note["id"]
                self.log.info(f"'{name}' ('{monica_id}'): Note '{note_id}' updated successfully")
                return
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                raise MonicaFetchError(f"'{name}' ('{monica_id}'): Error updating Monica note: {error}")

    def delete_note(self, note_id: str, monica_id: str, name: str) -> None:
        '''Creates a new note for a given contact id via api call.'''

        while True:
            # Create address
            response = requests.delete(self.base_url + f"/notes/{note_id}", headers=self.header, params=self.parameters)
            self.api_requests += 1

            # If successful
            if response.status_code == 200:
                self.updated_contacts[monica_id] = True
                self.log.info(f"'{name}' ('{monica_id}'): Note '{note_id}' deleted successfully")
                return
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                raise MonicaFetchError(f"'{name}' ('{monica_id}'): Error deleting Monica note: {error}")

    def remove_tags(self, data: dict, monica_id: str, name: str) -> None:
        '''Removes all tags given by id from a given contact id via api call.'''

        while True:
            # Create address
            response = requests.post(self.base_url + f"/contacts/{monica_id}/unsetTag", headers=self.header, params=self.parameters, json=data)
            self.api_requests += 1

            # If successful
            if response.status_code == 200:
                self.updated_contacts[monica_id] = True
                self.log.info(f"'{name}' ('{monica_id}'): Label(s) with id {data['tags']} removed successfully")
                return
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                raise MonicaFetchError(f"'{name}' ('{monica_id}'): Error removing Monica labels: {error}")

    def add_tags(self, data: dict, monica_id: str, name: str) -> None:
        '''Adds all tags given by name for a given contact id via api call.'''

        while True:
            # Create address
            response = requests.post(self.base_url + f"/contacts/{monica_id}/setTags", headers=self.header, params=self.parameters, json=data)
            self.api_requests += 1

            # If successful
            if response.status_code == 200:
                self.updated_contacts[monica_id] = True
                self.log.info(f"'{name}' ('{monica_id}'): Labels {data['tags']} assigned successfully")
                return
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                raise MonicaFetchError(f"'{name}' ('{monica_id}'): Error assigning Monica labels: {error}")


    def update_career(self, monica_id: str, data: dict) -> None:
        '''Updates job title and company for a given contact id via api call.'''
        # Initialization
        contact = self.get_contact(monica_id)
        name = contact['complete_name']

        while True:
            # Update contact
            response = requests.put(self.base_url + f"/contacts/{monica_id}/work", headers=self.header, params=self.parameters, json=data)
            self.api_requests += 1

            # If successful
            if response.status_code == 200:
                self.updated_contacts[monica_id] = True
                contact = response.json()['data']
                self.log.info(f"'{name}' ('{monica_id}'): Company and job title updated successfully")
                self.database.update(monica_id=monica_id, monica_last_changed=contact['updated_at'])
                return
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                self.log.warning(f"'{name}' ('{monica_id}'): Error updating Monica contact career info: {error}")

    def delete_address(self, address_id: str, monica_id: str, name: str) -> None:
        '''Deletes an address for a given address id via api call.'''
        while True:
            # Delete address
            response = requests.delete(self.base_url + f"/addresses/{address_id}", headers=self.header, params=self.parameters)
            self.api_requests += 1

            # If successful
            if response.status_code == 200:
                self.updated_contacts[monica_id] = True
                self.log.info(f"'{name}' ('{monica_id}'): Address '{address_id}' deleted successfully")
                return
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                raise MonicaFetchError(f"'{name}' ('{monica_id}'): Error deleting address '{address_id}': {error}")

    def create_address(self, data: dict, name: str) -> None:
        '''Creates an address for a given contact id via api call.'''
        # Initialization
        monica_id = data['contact_id']

        while True:
            # Create address
            response = requests.post(self.base_url + f"/addresses", headers=self.header, params=self.parameters, json=data)
            self.api_requests += 1

            # If successful
            if response.status_code == 201:
                self.updated_contacts[monica_id] = True
                address = response.json()['data']
                address_id = address["id"]
                self.log.info(f"'{name}' ('{monica_id}'): Address '{address_id}' created successfully")
                return
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                raise MonicaFetchError(f"'{name}' ('{monica_id}'): Error creating Monica address: {error}")

    def get_contact_fields(self, monica_id: str, name: str) -> List[dict]:
        '''Fetches all contact fields (phone numbers, emails, etc.) 
        for a given Monica contact id via api call.'''

        while True:
            # Get contact fields
            response = requests.get(self.base_url + f"/contacts/{monica_id}/contactfields", headers=self.header, params=self.parameters)
            self.api_requests += 1

            # If successful
            if response.status_code == 200:
                field_list = response.json()['data']
                return field_list
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                raise MonicaFetchError(f"'{name}' ('{monica_id}'): Error fetching Monica contact fields: {error}")

    def get_contact_field_id(self, type_name: str) -> str:
        '''Returns the id for a Monica contact field.'''
        # Fetch if not present yet
        if not self.contact_field_type_mapping:
            self.__get_contact_field_types()

        # Get contact field id
        field_id = self.contact_field_type_mapping.get(type_name, None)

        # No id is a serious issue
        if not field_id:
            raise InternalError(f"Could not find an id for contact field type '{type_name}'")

        return field_id
            
    def __get_contact_field_types(self) -> dict:
        '''Fetches all contact field types from Monica and saves them to a dictionary.'''

        while True:
        # Get genders
            response = requests.get(
                self.base_url + f"/contactfieldtypes", headers=self.header, params=self.parameters)
            self.api_requests += 1

            # If successful
            if response.status_code == 200:
                contact_field_types = response.json()['data']
                contact_field_type_mapping = {field['type']: field['id'] for field in contact_field_types}
                self.contact_field_type_mapping = contact_field_type_mapping
                return self.contact_field_type_mapping
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                self.log.error(f"Failed to fetch contact field types from Monica: {error}")
                raise MonicaFetchError("Error fetching contact field types from Monica!")


    def create_contact_field(self, monica_id: str, data: dict, name: str) -> None:
        '''Creates a contact field (phone number, email, etc.) 
        for a given Monica contact id via api call.'''

        while True:
            # Create contact field
            response = requests.post(self.base_url + f"/contactfields", headers=self.header, params=self.parameters, json=data)
            self.api_requests += 1

            # If successful
            if response.status_code == 201:
                self.updated_contacts[monica_id] = True
                contact_field = response.json()['data']
                field_id = contact_field["id"]
                type_desc = contact_field["contact_field_type"]["type"]
                self.log.info(f"'{name}' ('{monica_id}'): Contact field '{field_id}' ({type_desc}) created successfully")
                return
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                raise MonicaFetchError(f"'{name}' ('{monica_id}'): Error creating Monica contact field: {error}")

    def delete_contact_field(self, field_id: str, monica_id: str, name: str) -> None:
        '''Updates a contact field (phone number, email, etc.) 
        for a given Monica contact id via api call.'''

        while True:
            # Delete contact field
            response = requests.delete(self.base_url + f"/contactfields/{field_id}", headers=self.header, params=self.parameters)
            self.api_requests += 1

            # If successful
            if response.status_code == 200:
                self.updated_contacts[monica_id] = True
                self.log.info(f"'{name}' ('{monica_id}'): Contact field '{field_id}' deleted successfully")
                return
            else:
                error = response.json()['error']['message']
                if self.__is_slow_down_error(response, error):
                    continue
                raise MonicaFetchError(f"'{name}' ('{monica_id}'): Error deleting Monica contact field '{field_id}': {error}")

    def __is_slow_down_error(self, response: Response, error: str) -> bool:
        '''Checks if the error is an rate limiter error and slows down the requests if yes.'''
        if "Too many attempts, please slow down the request" in error:
            sec = int(response.headers.get('Retry-After'))
            print(f"\nToo many Monica requests, waiting {sec} seconds...")
            time.sleep(sec)
            return True
        else:
            return False

class MonicaContactUploadForm():
    '''Creates json form for creating or updating Monica contacts.'''

    def __init__(self, monica: Monica, first_name: str, last_name: str = None, nick_name: str = None,
                 middle_name: str = None, gender_type: str = 'O', birthdate_day: str = None,
                 birthdate_month: str = None, birthdate_year: str = None,
                 is_birthdate_age_based: bool = False, is_birthdate_known: bool = False,
                 is_deceased: bool = False, is_deceased_date_known: bool = False,
                 deceased_day: int = None, deceased_month: int = None,
                 deceased_year: int = None, deceased_age_based: bool = None,
                 create_reminders: bool = True) -> None:
        gender_id = monica.get_gender_mapping()[gender_type]
        self.data = {
            "first_name": first_name,
            "last_name": last_name,
            "nickname": nick_name,
            "middle_name": middle_name,
            "gender_id": gender_id,
            "birthdate_day": birthdate_day,
            "birthdate_month": birthdate_month,
            "birthdate_year": birthdate_year,
            "birthdate_is_age_based": is_birthdate_age_based,
            "deceased_date_add_reminder": create_reminders,
            "birthdate_add_reminder": create_reminders,
            "is_birthdate_known": is_birthdate_known,
            "is_deceased": is_deceased,
            "is_deceased_date_known": is_deceased_date_known,
            "deceased_date_day": deceased_day,
            "deceased_date_month": deceased_month,
            "deceased_date_year": deceased_year,
            "deceased_date_is_age_based": deceased_age_based,
        }
