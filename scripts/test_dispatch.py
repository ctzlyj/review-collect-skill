import unittest
from collect import platform_for
class Dispatch(unittest.TestCase):
 def test_only_advertise_implemented_platforms(self):
  self.assertEqual(platform_for('https://detail.tmall.com/item.htm?id=1'),'taobao')
  self.assertEqual(platform_for('https://mms.pinduoduo.com/'),'pdd')
  self.assertEqual(platform_for(None,'pdd'),'pdd')
  for name in ['jd','1688','douyin']:
   with self.assertRaises(ValueError):platform_for(None,name)
if __name__=='__main__':unittest.main()
