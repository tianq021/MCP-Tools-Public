import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pynvml

from app.resources import memory_snapshot, process_gpu_memory, resource_snapshot


class ResourceTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows process accounting')
    def test_own_process_and_system_memory_are_readable(self):
        result = memory_snapshot(os.getpid())
        self.assertGreater(result['total'], result['available'])
        self.assertGreater(result['available'], 0)
        self.assertGreater(result['process_rss'], 0)
        self.assertGreater(result['process_private'], 0)
        self.assertEqual(memory_snapshot()['process_rss'], 0)
        self.assertIsNone(memory_snapshot(0)['process_rss'])

    def test_gpu_duplicate_pid_is_not_double_counted(self):
        with patch('app.resources.pynvml.nvmlDeviceGetComputeRunningProcesses',return_value=[SimpleNamespace(pid=42,usedGpuMemory=100)]), \
             patch('app.resources.pynvml.nvmlDeviceGetGraphicsRunningProcesses',return_value=[SimpleNamespace(pid=42,usedGpuMemory=100),SimpleNamespace(pid=50,usedGpuMemory=500)]):
            self.assertEqual(process_gpu_memory(None,42,1000),100)
            self.assertIsNone(process_gpu_memory(None,43,1000))
            self.assertEqual(process_gpu_memory(None,None,1000),0)

    def test_wddm_and_unavailable_monitoring_do_not_claim_zero(self):
        for value in (None,2**64-1):
            with patch('app.resources.pynvml.nvmlDeviceGetComputeRunningProcesses',return_value=[SimpleNamespace(pid=42,usedGpuMemory=value)]):
                self.assertIsNone(process_gpu_memory(None,42,1000))
        with patch('app.resources.pynvml.nvmlDeviceGetComputeRunningProcesses',side_effect=pynvml.NVMLError_NotSupported), \
             patch('app.resources.pynvml.nvmlDeviceGetGraphicsRunningProcesses',side_effect=pynvml.NVMLError_NotSupported):
            self.assertIsNone(process_gpu_memory(None,42,1000))

    def test_gpu_monitor_failure_still_returns_memory(self):
        memory={'total':1000,'available':600,'process_rss':100,'process_private':200}
        with patch('app.resources.pynvml.nvmlInit',side_effect=pynvml.NVMLError_NotSupported), \
             patch('app.resources.memory_snapshot',return_value=memory) as read:
            self.assertEqual(resource_snapshot(42),{'memory':memory,'gpu':None})
            read.assert_called_once_with(42)
