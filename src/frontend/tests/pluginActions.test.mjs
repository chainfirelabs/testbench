import assert from 'node:assert/strict'
import test from 'node:test'

import { actionConfirmation } from '../src/pluginActionConfirmation.ts'

test('device info requires confirmation before starting an AI agent', () => {
  const message = actionConfirmation({
    plugin_id: 'device-info-agent', id: 'device-info-agent.research',
    entity: 'devices', scope: 'row', label: 'Info', risk: 'normal',
  }, 'router-01')

  assert.match(message, /Gather device info for “router-01”/)
  assert.match(message, /starts an AI agent to inspect the web page of the device to find device information/)
})

test('ordinary actions do not require confirmation', () => {
  assert.equal(actionConfirmation({
    plugin_id: 'network-scan', id: 'scan', entity: 'devices', scope: 'row',
    label: 'Scan', risk: 'normal',
  }, 'router-01'), null)
})

test('disruptive actions retain their warning', () => {
  assert.match(actionConfirmation({
    plugin_id: 'device-reboot', id: 'reboot', entity: 'devices', scope: 'row',
    label: 'Reboot', risk: 'disruptive',
  }, 'router-01'), /interrupts whatever the device is doing/)
})
