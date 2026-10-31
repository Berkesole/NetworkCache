import unittest
import network_backend as b
class BackendTests(unittest.TestCase):
 def test_each_lan_has_exactly_two_endpoints(self):
  self.assertEqual(b.point_to_point('node1[0]=a\nnode101[0]=a\nnode101[1]=b\nnode201[0]=b'),{'a':[('node1','0'),('node101','0')],'b':[('node101','1'),('node201','0')]})
 def test_reject_shared_lan_or_duplicate_endpoint(self):
  for text in ('','node1[0]=a','node1[0]=a\nnode2[0]=a\nnode3[0]=a','node1[0]=a\nnode1[0]=a'):
   with self.assertRaises(AssertionError):b.point_to_point(text)
if __name__=='__main__':unittest.main()
