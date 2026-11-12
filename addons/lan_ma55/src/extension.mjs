import { mountResults } from './results.mjs';
export { mountResults };
export default {
  id: 'lan.ma55', apiVersion: 1, version: '0.3.3',
  routes: [{ id: 'observer', path: '/lan-ma55', mount: mountResults }],
  navigation: [{ id: 'observer', routeId: 'observer', label: 'MA55 结构观察', icon: 'Activity' }],
};
