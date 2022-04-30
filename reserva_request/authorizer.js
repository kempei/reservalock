exports.handler = async (event) => {
    if (event.headers.authorization === "ichibatoken") {
        const response = {"isAuthorized": true};
        return response;
    }
    const response = {"isAuthorized": false};
    return response;
};