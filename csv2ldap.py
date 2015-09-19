"""
script to update ldap with contact comming from a csv file
see default.cfg for configuration option
"""

import re
import logging
import ldap
import ConfigParser
import argparse


def parse_csv_contacts(filename, attrs, columns, header_tag, def_mobile, def_fax, sep=';'):
    """
    read contacts from a csv file, default sep is ';'
    """

    # read the complete file in memory
    with open(filename) as f:
        lines = f.readlines()

    # an empty comtacts list
    contacts = []

    # parse each line of the file
    for (index, line) in enumerate(lines):
        records = line.split(sep)
        line_nbr = index + 1
        logging.debug('records {0}: {1}'.format(line_nbr, records))

        employee_number = records[columns[0]]
        building = records[columns[1]]
        office = records[columns[2]]
        phone = records[columns[3]]
        mobile = records[columns[4]]
        fax = records[columns[5]]

        # ignore header lines
        if employee_number == header_tag:
            continue

        # ignore empty line
        if ''.join(records) == '\n':
            continue

        # verifications
        if not employee_number:
            logging.warning("no employeeNumber on line {0}".format(line_nbr))
            continue
        if not building:
            logging.warning("no building on line {0}".format(line_nbr))
            continue
        if not office:
            logging.warning("no office on line {0}".format(line_nbr))
            continue
        if not phone or not re.match('\d{8}$', phone):
            logging.warning("no phone or bad phone number on line {0}".format(line_nbr))
            continue
        if fax and not re.match('\d{8}$', fax):
            logging.warning("bad fax number on line {0}".format(line_nbr))
            continue
        if mobile and not re.match('\d{9}$', mobile):
            logging.warning("bad mobile number on line {0}".format(line_nbr))
            continue

        # tranformations
        employee_number = '{0:05d}'.format(int(employee_number))
        phone = '+352 {0} {1}'.format(phone[:4], phone[4:])
        if mobile:
            mobile = '+352 {0} {1} {2}'.format(mobile[:3], mobile[3:6], mobile[6:])
        else:
            mobile = def_mobile
        if fax:
            fax = '+352 {0} {1}'.format(fax[:4], fax[4:])
        else:
            fax = def_fax

        # create contact and add to the contacts list
        contacts.append({
            attrs[0]: employee_number,
            attrs[1]: building,
            attrs[2]: office,
            attrs[3]: phone,
            attrs[4]: mobile,
            attrs[5]: fax,
        })

        # save the line number
        line_nbr_cache[employee_number] = line_nbr

    return contacts


def get_ldap_contact(ldap_conn, base_dn, employee_number, unique_id, attrs, cache):
    """
    read contact from the ldap
    """
    search_filter = '{0}={1}'.format(unique_id, employee_number)
    results = ldap_conn.search_s(base_dn, ldap.SCOPE_SUBTREE, search_filter, attrs)
    contact_found = {}
    if results:
        attrs_found = results[0][1]
        # cache the dn for the employee_number
        cache[employee_number] = results[0][0]
        for key in attrs:
            if key in attrs_found:
                contact_found[key] = attrs_found[key][0]
            else:
                contact_found[key] = False
    else:
        logging.warning('Cannot found employee in ldap ' + employee_number)
    return contact_found


def compare_contact(c1, c2, unique_id, attrs):
    """ compare 2 contact and return changes to go from c1 to c2"""
    changes = {}
    if c1 != c2:
        for key in attrs:
            if c1[unique_id] != c2[unique_id]:
                raise Exception('bad contact comparaison unique_id do not match!')
            # copy the unique_id
            changes[unique_id] = c1[unique_id]
            # copy all values that changed
            if c1[key] != c2[key]:
                changes[key] = c1[key]
    return changes


def update_ldap_contact(ldap_con, change, unique_id, cache):
    """ update a contact change in the ldap server """

    # attributes to change
    mod_attrs = []
    for key in change.keys():
        if key == unique_id:
            continue
        mod_attrs.append((ldap.MOD_REPLACE, key, change[key]))

    # get dn from cache
    dn = cache[change[unique_id]]
    logging.info('UPDATE: {0} {1}'.format(dn, mod_attrs))

    # update the ldap
    ldap_con.modify_s(dn, mod_attrs)


""" MAIN """

# get config filename from cmd line
parser = argparse.ArgumentParser()
parser.add_argument('config', help='configuration filename')
parser.add_argument('-u', '--update', help="update the ldap database", action="store_true")
args = parser.parse_args()
config_filename = args.config
update_ldap = args.update

# read the coonfig file
config = ConfigParser.RawConfigParser()
if not config.read(config_filename):
    raise Exception('cannot read config ' + config_filename)

logging_level = config.get('logging', 'level')
ldap_server = config.get('ldap', 'server')
ldap_user = config.get('ldap', 'user')
ldap_password = config.get('ldap', 'password')
ldap_basedn = config.get('ldap', 'basedn')
contact_attrs = config.get('contact', 'attrs').split()
contact_id = config.get('contact', 'id')
default_mobile = config.get('mobile', 'default')
default_fax = config.get('fax', 'default')
csv_file = config.get('input', 'file')
csv_columns = [int(x) for x in config.get('input', 'columns').split()]
csv_header_tag = config.get('input', 'header_tag')

# cache
dn_cache = {}
line_nbr_cache = {}

# set logging level
logging.basicConfig(level=eval('logging.' + logging_level))

# log command line settings
logging.info('CONFIG FILE: {0}'.format(config_filename))
logging.info('UPDATE LDAP: {0}'.format(update_ldap))

# open ldap connection
l = ldap.initialize('ldap://' + ldap_server)

# compare all csv and ldap contacts, and create a list of changes
contact_changes = []
for csv_contact in parse_csv_contacts(csv_file, contact_attrs, csv_columns,
                                      csv_header_tag, default_mobile, default_fax):
    logging.debug('CSV: ' + str(csv_contact))
    en = csv_contact[contact_id]
    ldap_contact = get_ldap_contact(l, ldap_basedn, en, contact_id, contact_attrs, dn_cache)
    logging.debug('LDAP: ' + str(ldap_contact))
    if not ldap_contact:
        logging.warning('CSV: ' + str(csv_contact))
    if ldap_contact:
        contact_change = compare_contact(csv_contact, ldap_contact, contact_id, contact_attrs)
        logging.debug('DIFF: ' + str(contact_change))
        if contact_change:
            contact_changes.append(contact_change)

# log changes
for contact_change in contact_changes:
    logging.info('CHANGE {0} : {1}'.format(line_nbr_cache[contact_change[contact_id]], contact_change))

# update ldap server
if update_ldap and contact_changes:
    l.simple_bind_s(ldap_user, ldap_password)
    for contact_change in contact_changes:
        update_ldap_contact(l, contact_change, contact_id, dn_cache)
