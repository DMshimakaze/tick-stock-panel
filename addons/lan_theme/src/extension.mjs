import { mountTheme } from './page.mjs';

export default {
  id:'lan.theme', apiVersion:1, version:'1.0.5',
  routes:[{id:'theme-filter',path:'/lan-theme',mount:mountTheme}],
  navigation:[{id:'theme-filter',routeId:'theme-filter',label:'题材与MA55',icon:'Table',order:530}],
};
