import {mountIndustry} from './page.mjs';

export default {
  id:'lan.sector_focus',apiVersion:1,version:'1.0.1',
  routes:[{id:'industry-focus',path:'/lan-industry',mount:mountIndustry}],
  navigation:[{id:'industry-focus',routeId:'industry-focus',label:'行业强度与MA55',icon:'BarChart3',order:525}],
};
