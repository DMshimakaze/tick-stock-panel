import { mountWorkbench } from './workbench.mjs';
export default {
  id: 'lan.ma55', apiVersion: 1, version: '0.13.10',
  routes: [{ id: 'observer', path: '/lan-ma55', mount: mountWorkbench }],
  navigation: [{ id: 'observer', routeId: 'observer', label: 'MA55 板块观察', icon: 'Activity' }],
};
