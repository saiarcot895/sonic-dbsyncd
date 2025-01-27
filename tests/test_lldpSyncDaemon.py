import os
import sys
# noinspection PyUnresolvedReferences
import tests.mock_tables.dbconnector


modules_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(modules_path, 'src'))

from unittest import TestCase
import json
import mock
import re
import lldp_syncd
import lldp_syncd.conventions
import lldp_syncd.daemon
from swsssdk import SonicV2Connector

INPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'subproc_outputs')


def create_dbconnector():
    db = SonicV2Connector()
    db.connect(db.APPL_DB)
    return db


def make_seconds(days, hours, minutes, seconds):
    """
    >>> make_seconds(0,5,9,5)
    18545
    """
    return seconds + (60 * minutes) + (60 * 60 * hours) + (24 * 60 * 60 * days)


class TestLldpSyncDaemon(TestCase):
    def setUp(self):
        with open(os.path.join(INPUT_DIR, 'lldpctl.json')) as f:
            self._json = json.load(f)

        with open(os.path.join(INPUT_DIR, 'lldpctl_mgmt_only.json')) as f:
            self._json_short = json.load(f)

        with open(os.path.join(INPUT_DIR, 'short_short.json')) as f:
            self._json_short_short = json.load(f)

        with open(os.path.join(INPUT_DIR, 'lldpctl_single_loc_mgmt_ip.json')) as f:
            self._single_loc_mgmt_ip = json.load(f)

        with open(os.path.join(INPUT_DIR, 'interface_only.json')) as f:
            self._interface_only = json.load(f)

        with open(os.path.join(INPUT_DIR, 'lldpctl_no_neighbors_loc_mgmt_ip.json')) as f:
            self._no_neighbors_loc_mgmt_ip = json.load(f)

        self.daemon = lldp_syncd.LldpSyncDaemon()

    def test_parse_json(self):
        jo = self.daemon.parse_update(self._json)
        print(json.dumps(jo, indent=3))

    def test_parse_short(self):
        jo = self.daemon.parse_update(self._json_short)
        print(json.dumps(jo, indent=3))

    def test_parse_short_short(self):
        jo = self.daemon.parse_update(self._json_short_short)
        print(json.dumps(jo, indent=3))

    def test_sync_roundtrip(self):
        parsed_update = self.daemon.parse_update(self._json)
        self.daemon.sync(parsed_update)
        db = create_dbconnector()
        keys = db.keys(db.APPL_DB)

        dump = {}
        for k in keys:
            # The test case is for LLDP neighbor information.
            # Need to filter LLDP_LOC_CHASSIS entry because the entry is removed from parsed_update after executing daemon.sync().
            if k != 'LLDP_LOC_CHASSIS':
                dump[k] = db.get_all(db.APPL_DB, k)
        print(json.dumps(dump, indent=3))

        # convert dict keys to ints for easy comparison
        jo = {'LLDP_ENTRY_TABLE:'+ k: v for k, v in parsed_update.items()}
        self.assertEqual(jo, dump)

        # test enumerations
        for k, v in dump.items():
            chassis_subtype = v['lldp_rem_chassis_id_subtype']
            chassis_id = v['lldp_rem_chassis_id']
            if int(chassis_subtype) == lldp_syncd.conventions.LldpChassisIdSubtype.macAddress:
                if re.match(r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$', chassis_id) is None:
                    self.fail("Non-mac returned for chassis ID")
            else:
                self.fail("Test data only contains chassis MACs")

    def test_timeparse(self):
        self.assertEqual(lldp_syncd.daemon.parse_time("0 day, 05:09:02"), make_seconds(0, 5, 9, 2))
        self.assertEqual(lldp_syncd.daemon.parse_time("2 days, 05:59:02"), make_seconds(2, 5, 59, 2))
        self.assertEqual(lldp_syncd.daemon.parse_time("-2 days, -23:-55:-02"), make_seconds(0, 0, 0, 0))

    def parse_mgmt_ip(self, json_file):
        parsed_update = self.daemon.parse_update(json_file)
        mgmt_ip_str = parsed_update['local-chassis'].get('lldp_loc_man_addr')
        json_chassis = json.dumps(json_file['lldp_loc_chassis']['local-chassis']['chassis'])
        chassis_dict = json.loads(json_chassis)
        json_mgmt_ip = list(chassis_dict.values())[0]['mgmt-ip']
        if isinstance(json_mgmt_ip, list):
            i=0
            for mgmt_ip in mgmt_ip_str.split(','):
                self.assertEqual(mgmt_ip, json_mgmt_ip[i])
                i+=1
        else:
            self.assertEqual(mgmt_ip_str, json_mgmt_ip)

    def test_multiple_mgmt_ip(self):
        self.parse_mgmt_ip(self._json)

    def test_single_mgmt_ip(self):
        self.parse_mgmt_ip(self._single_loc_mgmt_ip)

    def test_local_mgmt_ip_no_neighbors(self):
        self.parse_mgmt_ip(self._no_neighbors_loc_mgmt_ip)

    def test_loc_chassis(self):
        parsed_update = self.daemon.parse_update(self._json)
        parsed_loc_chassis = parsed_update['local-chassis']
        self.daemon.sync(parsed_update)
        db = create_dbconnector()
        db_loc_chassis_data = db.get_all(db.APPL_DB, 'LLDP_LOC_CHASSIS')
        self.assertEqual(parsed_loc_chassis, db_loc_chassis_data)

    def test_remote_sys_capability_list(self):
        interface_list = self._interface_only['lldp'].get('interface')
        for interface in interface_list:
            (if_name, if_attributes), = interface.items()
            capability_list = self.daemon.get_sys_capability_list(if_attributes, if_name, "fake_chassis_id")
            self.assertNotEqual(capability_list, [])
