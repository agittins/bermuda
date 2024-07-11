from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares

# from scipy.spatial.distance import pdist, squareform
from sklearn.manifold import MDS

# import matplotlib.pyplot as plt


def calculate_proxy_positions(proxy_distances):
    # Handle missing data in proxy distances
    proxy_distances_masked = np.ma.masked_invalid(proxy_distances)
    proxy_distances_filled = proxy_distances_masked.filled(
        proxy_distances_masked.mean()
    )

    # Perform multidimensional scaling
    mds = MDS(
        n_components=2,
        dissimilarity="precomputed",
        random_state=42,
        normalized_stress="auto",
    )
    proxy_positions = mds.fit_transform(proxy_distances_filled)

    # Print the results
    print("Proxy positions:", proxy_positions)
    print()

    return proxy_positions


def localize_beacon(
    proxy_positions,
    proxy_names,
    beacon_name,
    beacon_distances,
    mirror_x=False,
    mirror_y=False,
):
    def trilateration(distances, positions):
        def error(x, c, r):
            return np.linalg.norm(x - c, axis=1) - r

        position_count = len(positions)
        S = sum(distances)
        # Approximate initial guess
        x0 = [
            sum([positions[i][0] * distances[i] / S for i in range(position_count)]),
            sum([positions[i][1] * distances[i] / S for i in range(position_count)]),
        ]
        res = least_squares(error, x0, args=(positions, distances))
        return res.x

    # Handle missing data in beacon distances
    beacon_distances_masked = np.ma.masked_invalid(beacon_distances)
    valid_indices = np.where(~beacon_distances_masked.mask)[0]
    valid_beacon_distances = beacon_distances_masked[valid_indices].data
    valid_proxy_positions = proxy_positions[valid_indices]

    # Calculate beacon position using trilateration with valid data
    beacon_position = trilateration(valid_beacon_distances, valid_proxy_positions)

    # Calculate distances from beacon to each proxy
    distances_to_proxies = np.linalg.norm(beacon_position - proxy_positions, axis=1)

    # Calculate probabilities based on inverse distances
    # Note: Probabilities are more an indicator of relative proximity than a true statistical probability
    inverse_distances = 1 / distances_to_proxies
    probabilities = inverse_distances / np.sum(inverse_distances)

    # Mirror the positions if requested
    if mirror_x:
        proxy_positions[:, 0] *= -1
        beacon_position[0] *= -1
    if mirror_y:
        proxy_positions[:, 1] *= -1
        beacon_position[1] *= -1

    # Print the results
    print("Beacon name:", beacon_name)
    print("Beacon position:", beacon_position)
    print("Distances to proxies:", distances_to_proxies)
    print("Probabilities:")
    for i in range(len(proxy_names)):
        print(f"{proxy_names[i]}: {probabilities[i]:.3f}")

    # # Plot the positions
    # plt.figure(figsize=(8, 6))
    # proxy_x = [x for x, y in proxy_positions]
    # proxy_y = [y for x, y in proxy_positions]
    # x_lim = max(abs(min(proxy_x)), abs(max(proxy_x)))
    # y_lim = max(abs(min(proxy_y)), abs(max(proxy_y))) * 1.1
    # lim = max(x_lim, y_lim)
    # for i, (x, y) in enumerate(proxy_positions):
    #     plt.scatter(x, y, marker='o')
    #     plt.annotate(proxy_names[i], (x, y), textcoords="offset points", xytext=(0, -15), ha='center')

    # beacon_x, beacon_y = beacon_position
    # plt.scatter(beacon_x, beacon_y, marker='*', s=200, c='r')
    # plt.annotate(beacon_name, (beacon_x, beacon_y), textcoords="offset points", xytext=(0, -15), ha='center')

    # # plt.legend()
    # plt.xlabel('X')
    # plt.ylabel('Y')
    # plt.xlim(-lim, lim)
    # plt.ylim(-lim, lim)
    # plt.gca().set_aspect('equal', adjustable='box')
    # plt.title('Positions of Proxies and Beacon')
    # plt.show()

    return beacon_position, distances_to_proxies, probabilities


# Proxy data in metres from each other
proxy_names = ["Office", "Garage", "Living room", "Gate", "Kitchen"]
proxy_distances = np.array(
    [
        [0.0, 8.0, 11.0, 17.0, 9.5],
        [8.0, 0.0, 15.0, 19.0, 15.0],
        [11.0, 15.0, 0.0, 7.0, 2.5],
        [17.0, 19.0, 7.0, 0.0, 9.5],
        [9.5, 15.0, 2.5, 9.5, 0.0],
    ]
)

proxy_positions = calculate_proxy_positions(proxy_distances)

# Beacon data in meters
beacon_name = "iPhone1"
beacon_distances = np.array([2, 9, 10, np.nan, 8.5])
beacon_position, distances_to_proxies, probabilities = localize_beacon(
    proxy_positions,
    proxy_names,
    beacon_name,
    beacon_distances,
    mirror_x=True,
    mirror_y=True,
)

beacon_name = "iPhone2"
beacon_distances = np.array([2, 6, 10.5, 16, 9.5])
beacon_position, distances_to_proxies, probabilities = localize_beacon(
    proxy_positions,
    proxy_names,
    beacon_name,
    beacon_distances,
    mirror_x=False,
    mirror_y=False,
)
