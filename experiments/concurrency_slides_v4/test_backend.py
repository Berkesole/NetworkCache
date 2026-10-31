import unittest,copy
import network_backend as b
class BackendTests(unittest.TestCase):
 def test_actual_tc_nested_schema(self):
  opts={'limit':1000,'delay':{'delay':.002,'jitter':0,'correlation':0},'rate':{'rate':125000000,'packetoverhead':0,'cellsize':0,'celloverhead':0},'ecn':False,'gap':0}
  b.validate_qdisc(opts)
  for path,value in [(('limit',),2000),(('delay','delay'),.001),(('delay','jitter'),.001),(('rate','rate'),250000000),(('rate','packetoverhead'),20),(('loss',),.01),(('delay',),.002)]:
   bad=copy.deepcopy(opts);obj=bad
   for k in path[:-1]:obj=obj[k]
   obj[path[-1]]=value
   with self.assertRaises(AssertionError):b.validate_qdisc(bad)
 def test_each_lan_has_exactly_two_endpoints(self):
  self.assertEqual(b.point_to_point('node1[0]=a\nnode101[0]=a\nnode101[1]=b\nnode201[0]=b'),{'a':[('node1','0'),('node101','0')],'b':[('node101','1'),('node201','0')]})
 def test_reject_shared_lan_or_duplicate_endpoint(self):
  for text in ('','node1[0]=a','node1[0]=a\nnode2[0]=a\nnode3[0]=a','node1[0]=a\nnode1[0]=a'):
   with self.assertRaises(AssertionError):b.point_to_point(text)
if __name__=='__main__':unittest.main()
