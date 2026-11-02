import unittest
from .ha_devices import action_payload, controls, intent


class ThermostatTests(unittest.TestCase):
    def attrs(self, **extra):
        result = dict(supported_features=3, _state='heat_cool', current_temperature=66,
                      target_temp_low=70, target_temp_high=76, min_temp=40, max_temp=99,
                      _temperature_gap=6)
        result.update(extra)
        return result

    def payload(self, action, target, **extra):
        return action_payload('climate.test', self.attrs(**extra), action, target)[1]

    def test_single_mode_target_still_works(self):
        for mode in ('heat', 'cool'):
            self.assertEqual(self.payload('temperature', 65, _state=mode), dict(entity_id='climate.test', temperature=65))

    def test_generic_auto_target_requires_explicit_direction(self):
        for target in (65, 70, 73, 76, 78):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'Say set the heat'):
                self.payload('temperature', target)

    def test_heat_changes_only_heat_regardless_of_room(self):
        for room in (50, 66, 80, None, float('nan')):
            self.assertEqual(self.payload('heating_temperature', 65, current_temperature=room),
                             dict(entity_id='climate.test', target_temp_low=65, target_temp_high=76))

    def test_cool_changes_only_cool(self):
        self.assertEqual(self.payload('cooling_temperature', 71, target_temp_low=65),
                         dict(entity_id='climate.test', target_temp_low=65, target_temp_high=71))

    def test_gap_conflict_refuses_instead_of_moving_other_limit(self):
        for action, target in [('heating_temperature', 71), ('cooling_temperature', 75)]:
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'change the other setpoint first'):
                self.payload(action, target)
        with self.assertRaises(ValueError): self.payload('heating_temperature', 65, target_temp_low=59, target_temp_high=65)
        self.assertEqual(self.payload('cooling_temperature', 71, target_temp_low=59, target_temp_high=65),
                         dict(entity_id='climate.test', target_temp_low=59, target_temp_high=71))

    def test_celsius_decimals_and_range_only_device(self):
        self.assertEqual(self.payload('heating_temperature', 19.5, supported_features=2,
                                     target_temp_low=20, target_temp_high=23, _temperature_gap=3, min_temp=7, max_temp=35),
                         dict(entity_id='climate.test', target_temp_low=19.5, target_temp_high=23))
        self.assertIn('heating_temperature', [c['key'] for c in controls('climate.test', dict(supported_features=2))])
        with self.assertRaises(ValueError): self.payload('heating_temperature', 65, _state='heat', supported_features=2)

    def test_invalid_data_never_makes_payload(self):
        for extra in [dict(target_temp_low=None), dict(target_temp_high=60), dict(_temperature_gap=-1),
                      dict(_temperature_gap=True), dict(supported_features=1)]:
            with self.subTest(extra=extra), self.assertRaises(ValueError): self.payload('heating_temperature', 65, **extra)
        for target in (True, float('inf'), 39, 100):
            with self.subTest(target=target), self.assertRaises(ValueError): self.payload('heating_temperature', target)

    def test_explicit_phrases_names_and_negation(self):
        for phrase in ('Set thermostat heating to 65 degrees', 'Set heating of the thermostat to 65',
                       'Set the heat setpoint to 65 degrees', 'Set the heat to 65'):
            self.assertEqual(intent(phrase, 'Thermostat', 'climate.test'), ('heating_temperature', 65))
        self.assertEqual(intent('Set the cooling to 71', 'Thermostat', 'climate.test'), ('cooling_temperature', 71))
        self.assertEqual(intent('Set heat to 65.5 degrees', 'Thermostat', 'climate.test'), ('heating_temperature', 65.5))
        self.assertIsNone(intent('Set cooling to 65 degrees', 'Lamp', 'light.test'))
        self.assertIsNone(intent('Do not set heating to 65', 'Thermostat', 'climate.test'))
        self.assertIsNone(intent('Set another thermostat heating to 65', 'Thermostat', 'climate.test'))

    def test_explicit_target_never_switches_mode(self):
        self.assertEqual(self.payload('heating_temperature', 65, _state='heat'), dict(entity_id='climate.test', temperature=65))
        self.assertEqual(self.payload('cooling_temperature', 71, _state='cool'), dict(entity_id='climate.test', temperature=71))
        for mode,action in [('heat','cooling_temperature'),('cool','heating_temperature'),('off','heating_temperature')]:
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.payload(action, 65, _state=mode)

    def test_zero_gap_allows_close_and_equal_targets_but_not_crossing(self):
        for gap in (0, None):
            self.assertEqual(self.payload('heating_temperature', 75, _temperature_gap=gap),
                             dict(entity_id='climate.test', target_temp_low=75, target_temp_high=76))
            self.assertEqual(self.payload('heating_temperature', 76, _temperature_gap=gap),
                             dict(entity_id='climate.test', target_temp_low=76, target_temp_high=76))
            self.assertEqual(self.payload('cooling_temperature', 70, _temperature_gap=gap),
                             dict(entity_id='climate.test', target_temp_low=70, target_temp_high=70))
            self.assertEqual(self.payload('heating_temperature', 65, _temperature_gap=gap, target_temp_low=65, target_temp_high=65),
                             dict(entity_id='climate.test', target_temp_low=65, target_temp_high=65))
            with self.assertRaises(ValueError): self.payload('heating_temperature', 77, _temperature_gap=gap)
            with self.assertRaises(ValueError): self.payload('cooling_temperature', 69, _temperature_gap=gap)


if __name__ == '__main__': unittest.main()
