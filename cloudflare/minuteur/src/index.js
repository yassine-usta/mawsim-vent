// Entrée du Worker du minuteur : workerd ne permet d'exporter ici que des gestionnaires. La logique est dans minuteur.js.
import { tour } from "./minuteur.js";

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(tour(env, new Date(controller.scheduledTime)));
  },
};
