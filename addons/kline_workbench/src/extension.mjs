import { mountWorkbench } from './page.mjs';
export default {
  id: 'lan.kline', apiVersion: 1, version: '3.1.0',
  routes: [{ id: 'workbench', path: '/lan-kline', mount: mountWorkbench }],
  navigation: [{ id: 'workbench', routeId: 'workbench', label: '分钟 K 工作台', icon: 'BarChart3' }],
};
