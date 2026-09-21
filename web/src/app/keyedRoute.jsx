import { useParams } from 'react-router-dom';

/**
 * Render a screen afresh for each value of a route parameter.
 *
 * React Router keeps the same element mounted when only a param changes, so going from
 * one conversation to another (a roster click on a desktop, a contact card's Message)
 * reused the whole screen: the previous agent's messages and pending approval stayed
 * drawn under the new header until the new data arrived, its polls kept running and
 * wrote into the new thread, and whatever was typed in the composer stayed in the box.
 * A key makes a different thread a different screen, and the old one's timers are
 * cleaned up with it.
 */
export function keyedBy(param, Component) {
  return function KeyedRoute(props) {
    const value = useParams()[param];
    return <Component key={value} {...props} />;
  };
}
