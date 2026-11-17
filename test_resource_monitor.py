import unittest
from unittest.mock import patch
import resource_monitor as monitor


class ResourceMonitorTests(unittest.TestCase):
    def test_page_size_and_swap_units(self):
        vm=monitor.parse_vm('Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 3.\nPages occupied by compressor: 2.')
        self.assertEqual(vm['compressed_bytes'],32768)
        self.assertEqual(vm['free_pages_bytes'],49152)
        self.assertEqual(monitor.parse_swap('vm.swapusage: total = 2.00G used = 512.00M free = 1.50G')['used_bytes'],512*1024**2)
        self.assertIsNone(monitor.parse_vm(None)['compressed_bytes'])
        self.assertEqual(monitor.parse_swap('unavailable'),{})

    def test_only_app_and_model_processes_are_exposed(self):
        def fake(args):
            if args[0]=='/bin/ps':return '100 1 10 /python\n110 100 20 /app/Aurora\n120 100 30 /bin/llama-server\n300 1 40 /bin/llama-server\n999 1 50 /other/private-app\n111 100 5 /bin/ps\n'
            if args[0]=='/usr/sbin/lsof':return 'p120' if '-iTCP:8082' in args else 'p300'
            if args[-1]=='hw.memsize':return str(24*1024**3)
            if args[-1]=='kern.memorystatus_vm_pressure_level':return '2'
            if args[-1]=='vm.swapusage':return 'total = 2G used = 1G free = 1G'
            return 'page size of 16384 bytes\nPages occupied by compressor: 7.'
        with patch.object(monitor.sys,'platform','darwin'),patch.object(monitor,'command',side_effect=fake):
            result=monitor.sample(100,'/app/Aurora',{'gemma':120},{'gemma':8082,'qwen27b':8084})
        self.assertEqual(result['rss_sum_bytes'],60*1024)
        self.assertEqual([p['pid'] for p in result['processes']],[100,110,120,300])
        self.assertEqual(result['processes'][-1]['kind'],'external_model')
        self.assertNotIn('private-app',str(result))
        self.assertEqual(result['system']['pressure'],'warning')
        self.assertEqual(result['system']['total_bytes'],24*1024**3)

    def test_permission_denied_is_unknown_not_zero(self):
        with patch.object(monitor.sys,'platform','darwin'),patch.object(monitor,'command',return_value=None):
            result=monitor.sample(100,'/app/Aurora',{}, {})
        self.assertFalse(result['available'])
        self.assertIsNone(result['rss_sum_bytes'])
        self.assertIsNone(result['system']['total_bytes'])
        self.assertEqual(result['system']['pressure'],'unknown')
        self.assertEqual(result['system']['swap'],{})

    def test_cache_and_model_change_invalidation(self):
        with patch.object(monitor,'sample',return_value={'available':True}) as sample:
            monitor.CACHE.update(at=0,value=None,key=None)
            monitor.snapshot(100,'/app/Aurora',{}, {})
            monitor.snapshot(100,'/app/Aurora',{}, {})
            self.assertEqual(sample.call_count,1)
            monitor.snapshot(100,'/app/Aurora',{'gemma':120}, {})
            self.assertEqual(sample.call_count,2)

    def test_vm_categories_and_used_estimate(self):
        text = 'page size of 16384 bytes\nPages wired down: 3.\nAnonymous pages: 7.\nPages occupied by compressor: 2.\nFile-backed pages: 5.'
        stats = monitor.parse_vm(text)
        self.assertEqual(stats['wired_bytes'], 3 * 16384)
        self.assertEqual(stats['anonymous_bytes'], 7 * 16384)
        self.assertEqual(stats['file_backed_bytes'], 5 * 16384)
        def reply(args):
            if args[0] == '/usr/bin/vm_stat': return text
            if args[-1] == 'hw.memsize': return str(24 * 1024**3)
            return None
        with patch.object(monitor.sys, 'platform', 'darwin'), patch.object(monitor, 'command', side_effect=reply):
            result = monitor.sample(100, '/app/Aurora', {}, {})
        self.assertEqual(result['system']['used_estimate_bytes'], 12 * 16384)
        self.assertIn('估算', result['system']['estimate_method'])

    def test_command_timeout_is_nonfatal(self):
        import subprocess
        with patch.object(monitor.subprocess,'run',side_effect=subprocess.TimeoutExpired('ps',2)):
            self.assertIsNone(monitor.command(['/bin/ps']))

if __name__=='__main__':unittest.main()
