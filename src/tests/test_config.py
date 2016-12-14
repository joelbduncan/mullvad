import ConfigParser
import os
import unittest

from mullvad import config

TEST_SETTINGS = {
    'integer': '42',
    'string': 'foo',
    'boolean': 'True',
}
config._DEFAULT_SETTINGS = TEST_SETTINGS


class TestReadOnlySettings(unittest.TestCase):
    def setUp(self):
        self.settings = config.ReadOnlySettings()

    def test_has_option(self):
        self.assertTrue(self.settings.has_option('integer'))
        self.assertFalse(self.settings.has_option('aaaaa'))

    def test_get(self):
        self.assertEqual(self.settings.get('integer'), '42')
        with self.assertRaises(ConfigParser.NoOptionError):
            self.settings.get('foo')

    def test_get_or_none(self):
        self.assertEqual(self.settings.get_or_none('integer'), '42')
        self.assertIsNone(self.settings.get_or_none('aaaaa'))

    def test_getboolean(self):
        self.assertIsInstance(self.settings.getboolean('boolean'), bool)
        with self.assertRaises(ValueError):
            self.settings.getboolean('string')

    def test_getint(self):
        self.assertIsInstance(self.settings.getint('integer'), int)
        with self.assertRaises(ValueError):
            self.settings.getint('boolean')


class TestSettings(unittest.TestCase):
    def setUp(self):
        self.settings = config.Settings('.')

    def tearDown(self):
        os.remove('settings.ini')

    def test_has_option(self):
        self.assertTrue(self.settings.has_option('integer'))

    def test_set(self):
        self.settings.set('integer', 50)
        self.assertEqual(self.settings.get('integer'), '50')


if __name__ == '__main__':
    unittest.main()
