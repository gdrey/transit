import unittest
import manual_edits

class BusColorTest(unittest.TestCase):
 def test_expand_all_standard_buses_and_preserve_other_modes(self):
  routes=[{'route_id':str(i),'route_type':'3'} for i in range(120)]+[{'route_id':'rail','route_type':'2'},{'route_id':'trolley','route_type':'11'}]
  result=manual_edits.interpret('change all of the buses to have the route color orange',routes)
  self.assertEqual(len(result['edits']),120)
  self.assertTrue(all(x['value']=='FFA500' and x['route_id'] not in ('rail','trolley') for x in result['edits']))
 def test_qualifier_does_not_trigger_all_bus_shortcut(self):
  self.assertIsNone(manual_edits.all_bus_color_request('make all buses orange except route 5',[{'route_id':'5','route_type':'3'}]))
 def test_no_bus_routes_gives_clear_error(self):
  with self.assertRaisesRegex(ValueError,'No bus routes'):
   manual_edits.all_bus_color_request('make all buses orange',[{'route_id':'R','route_type':'2'}])
if __name__=='__main__':unittest.main()
